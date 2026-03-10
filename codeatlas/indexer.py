"""Indexer for CodeAtlas - coordinates parsing and storage."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import xxhash

from .config import Config
from .parser.base import BaseParser, ParseResult, SymbolInfo
from .parser.cpp import CppParser
from .parser.python import PythonParser
from .storage.db import Database, EdgeRecord, FileEdgeRecord, FileRecord, NamespaceRecord, SymbolRecord


@dataclass
class SymbolContext:
    """Symbol with its file context for merging."""
    symbol: SymbolInfo
    file_id: int
    file_path: str
    language: str


class Indexer:
    """Coordinates file discovery, parsing, and storage."""

    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.parsers: dict[str, BaseParser] = {}
        self._register_parsers()
        # Cache for trust inheritance during rebuild
        self._trust_cache: dict[str, tuple[str, str]] = {}  # signature_hash -> (trust_level, trust_source)

    def _register_parsers(self) -> None:
        python_parser = PythonParser()
        for ext in python_parser.file_extensions:
            self.parsers[ext] = python_parser

        cpp_parser = CppParser()
        for ext in cpp_parser.file_extensions:
            self.parsers[ext] = cpp_parser

    def _save_trust_cache(self, file_paths: Optional[set[str]] = None) -> None:
        """Save trust levels from existing symbols for inheritance.

        Args:
            file_paths: If provided, only cache symbols from these files.
                       If None, cache all symbols.
        """
        self._trust_cache.clear()
        all_symbols = self.db.get_all_symbols()

        for symbol in all_symbols:
            # Filter by file paths if specified
            if file_paths is not None:
                file_record = self.db.get_file_by_id(symbol.file_id)
                if not file_record or file_record.path not in file_paths:
                    continue

            # Only cache non-default trust levels
            if symbol.trust_source != "default":
                self._trust_cache[symbol.signature_hash] = (
                    symbol.trust_level,
                    symbol.trust_source,
                )

    def _get_inherited_trust(
        self, signature_hash: str, default_level: str, default_source: str
    ) -> tuple[str, str]:
        """Get inherited trust level from cache or return default."""
        if signature_hash in self._trust_cache:
            old_level, old_source = self._trust_cache[signature_hash]
            # Preserve explicit and config trust, mark as inherited
            if old_source in ("explicit", "config"):
                return old_level, "inherited"
            elif old_source == "inherited":
                return old_level, "inherited"
        return default_level, default_source

    def index_workspace(self, target_path: Optional[Path] = None) -> tuple[int, int, list[str]]:
        """Index workspace or a specific path.

        Args:
            target_path: If provided, only index this file or directory.
                        If None, index entire workspace.
        """
        files_indexed = 0
        symbols_indexed = 0
        errors: list[str] = []

        # Discover files to index
        files_to_index = self._discover_files(target_path)

        # For partial rebuild, save trust cache for affected files
        if target_path is not None:
            affected_paths = {str(f) for f in files_to_index}
            self._save_trust_cache(affected_paths)

        # Phase 1: Parse files in parallel (no DB operations)
        parsed_files: list[tuple[Path, str, str, ParseResult]] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._parse_file_content, file_path): file_path
                for file_path in files_to_index
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    parsed_files.append(result)

        # Begin transaction for batch operations
        self.db.begin_transaction()

        try:
            # Phase 1b: Store parsed files to database (sequential)
            parse_results: list[tuple[int, ParseResult]] = []
            for file_path, language, content_hash, parse_result in parsed_files:
                file_id, parse_result, file_errors = self._store_parsed_file(
                    file_path, language, content_hash, parse_result
                )
                files_indexed += 1
                errors.extend(file_errors)
                parse_results.append((file_id, parse_result))

            # Phase 2: Merge and store symbols (handles decl/def merging for C++)
            symbols_indexed = self._store_merged_symbols(parse_results)

            # Cache symbols once for phases 3 and 4
            all_symbols = self.db.get_all_symbols()

            # Phase 3: Store edges using cached symbol table
            self._store_all_edges(parse_results, all_symbols)

            # Phase 4: Build namespace_members for C++ namespaces
            self._build_namespace_members(all_symbols)

            # Commit all changes
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return files_indexed, symbols_indexed, errors

    def _discover_files(self, target_path: Optional[Path] = None) -> list[Path]:
        """Discover files to index.

        Args:
            target_path: If provided, only discover files in this path.
                        Can be a single file or a directory.
        """
        files: list[Path] = []

        # Only index extensions that are both in config.extensions and have a parser
        allowed_extensions = self.config.extensions & set(self.parsers.keys())

        if target_path is not None:
            # Resolve to absolute path
            if not target_path.is_absolute():
                target_path = self.config.workspace / target_path
            target_path = target_path.resolve()

            if target_path.is_file():
                # Single file
                if target_path.suffix in allowed_extensions:
                    if not self.config.should_exclude(target_path):
                        files.append(target_path)
            elif target_path.is_dir():
                # Directory - discover all matching files
                for ext in allowed_extensions:
                    for file_path in target_path.rglob(f"*{ext}"):
                        if not self.config.should_exclude(file_path):
                            files.append(file_path)
        else:
            # Full workspace discovery
            for ext in allowed_extensions:
                for file_path in self.config.workspace.rglob(f"*{ext}"):
                    if not self.config.should_exclude(file_path):
                        files.append(file_path)

        return sorted(files)

    def _parse_file_content(
        self, file_path: Path
    ) -> Optional[tuple[Path, str, str, ParseResult]]:
        """Parse file content (thread-safe, no DB operations).

        Returns: (file_path, language, content_hash, parse_result) or None
        """
        parser = self._get_parser(file_path)
        if not parser:
            return None

        try:
            source = file_path.read_text(encoding="utf-8")
        except Exception:
            return None

        content_hash = xxhash.xxh64(source.encode()).hexdigest()
        rel_path = file_path.relative_to(self.config.workspace)
        parse_result = parser.parse(rel_path, source)

        return (file_path, parser.language, content_hash, parse_result)

    def _store_parsed_file(
        self, file_path: Path, language: str, content_hash: str, parse_result: ParseResult
    ) -> tuple[int, ParseResult, list[str]]:
        """Store parsed file to database (not thread-safe)."""
        existing_file = self.db.get_file_by_path(str(file_path))
        if existing_file:
            self._clear_file_data(existing_file.id)  # type: ignore

        file_id = self._store_file(file_path, language, content_hash)

        return (file_id, parse_result, parse_result.errors)

    def _store_merged_symbols(
        self, parse_results: list[tuple[int, ParseResult]]
    ) -> int:
        """Merge and store symbols, handling C++ declaration/definition merging."""
        # Collect all symbols with their file context
        all_symbols: list[SymbolContext] = []
        for file_id, result in parse_results:
            for symbol in result.symbols:
                all_symbols.append(SymbolContext(
                    symbol=symbol,
                    file_id=file_id,
                    file_path=result.file_path,
                    language=result.language,
                ))

        # Group by signature_hash for C++ (Python symbols stay separate)
        hash_groups: dict[str, list[SymbolContext]] = {}
        for ctx in all_symbols:
            key = ctx.symbol.signature_hash
            if key not in hash_groups:
                hash_groups[key] = []
            hash_groups[key].append(ctx)

        # Store merged symbols
        now = datetime.now().isoformat()
        qualified_to_id: dict[str, int] = {}
        symbols_stored = 0

        for sig_hash, contexts in hash_groups.items():
            if len(contexts) == 1:
                # Single symbol - store as-is
                ctx = contexts[0]
                symbol_id = self._store_single_symbol(ctx, qualified_to_id, now)
                qualified_to_id[ctx.symbol.qualified_name] = symbol_id
                symbols_stored += 1
            else:
                # Multiple symbols with same signature - merge decl/def
                symbol_id = self._store_merged_symbol(contexts, qualified_to_id, now)
                for ctx in contexts:
                    qualified_to_id[ctx.symbol.qualified_name] = symbol_id
                symbols_stored += 1

        return symbols_stored

    def _store_single_symbol(
        self,
        ctx: SymbolContext,
        qualified_to_id: dict[str, int],
        now: str,
    ) -> int:
        """Store a single symbol (no merging needed)."""
        symbol = ctx.symbol
        default_trust, default_source = self.config.get_trust_level(ctx.file_path, True)
        # Try to inherit trust level from previous version
        trust_level, trust_source = self._get_inherited_trust(
            symbol.signature_hash, default_trust, default_source
        )

        parent_id = None
        if symbol.parent_qualified_name:
            parent_id = qualified_to_id.get(symbol.parent_qualified_name)

        # Determine symbol_status for C++ (declaration_only/definition_only)
        symbol_status: Optional[str] = None
        if ctx.language == "cpp":
            if not symbol.is_definition:
                symbol_status = "declaration_only"
            # Note: definition_only would be set in _store_merged_symbol
            # when we have a definition without matching declaration

        # For single symbols, decl and def are the same
        record = SymbolRecord(
            id=None,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            signature_hash=symbol.signature_hash,
            symbol_kind=symbol.symbol_kind,
            language=ctx.language,
            file_id=ctx.file_id,
            parent_symbol_id=parent_id,
            decl_file_id=ctx.file_id,
            decl_start=symbol.start_line,
            decl_end=symbol.end_line,
            def_file_id=ctx.file_id,
            def_start=symbol.start_line,
            def_end=symbol.end_line,
            trust_level=trust_level,
            trust_source=trust_source,
            visibility=symbol.visibility,
            symbol_status=symbol_status,
            updated_at=now,
        )
        return self.db.insert_symbol(record)

    def _store_merged_symbol(
        self,
        contexts: list[SymbolContext],
        qualified_to_id: dict[str, int],
        now: str,
    ) -> int:
        """Merge and store symbols with same signature_hash (C++ decl/def)."""
        # Find declaration and definition
        decl_ctx: Optional[SymbolContext] = None
        def_ctx: Optional[SymbolContext] = None

        for ctx in contexts:
            if ctx.symbol.is_definition:
                def_ctx = ctx
            else:
                decl_ctx = ctx

        # Use definition as primary if available, otherwise declaration
        primary = def_ctx or decl_ctx
        if primary is None:
            primary = contexts[0]

        symbol = primary.symbol
        default_trust, default_source = self.config.get_trust_level(primary.file_path, True)
        # Try to inherit trust level from previous version
        trust_level, trust_source = self._get_inherited_trust(
            symbol.signature_hash, default_trust, default_source
        )

        parent_id = None
        if symbol.parent_qualified_name:
            parent_id = qualified_to_id.get(symbol.parent_qualified_name)

        # Determine symbol_status based on what we have
        symbol_status: Optional[str] = None
        if primary.language == "cpp":
            if decl_ctx is None and def_ctx is not None:
                # Only definition, no declaration
                symbol_status = "definition_only"
            elif decl_ctx is not None and def_ctx is None:
                # Only declaration, no definition
                symbol_status = "declaration_only"
            # Both present: symbol_status stays None (normal)

        # Set decl_* from declaration, def_* from definition
        if decl_ctx:
            decl_file_id = decl_ctx.file_id
            decl_start = decl_ctx.symbol.start_line
            decl_end = decl_ctx.symbol.end_line
        else:
            decl_file_id = primary.file_id
            decl_start = symbol.start_line
            decl_end = symbol.end_line

        if def_ctx:
            def_file_id = def_ctx.file_id
            def_start = def_ctx.symbol.start_line
            def_end = def_ctx.symbol.end_line
        else:
            def_file_id = primary.file_id
            def_start = symbol.start_line
            def_end = symbol.end_line

        # Prefer visibility from declaration (usually in header)
        visibility = decl_ctx.symbol.visibility if decl_ctx else symbol.visibility

        record = SymbolRecord(
            id=None,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            signature_hash=symbol.signature_hash,
            symbol_kind=symbol.symbol_kind,
            language=primary.language,
            file_id=primary.file_id,
            parent_symbol_id=parent_id,
            decl_file_id=decl_file_id,
            decl_start=decl_start,
            decl_end=decl_end,
            def_file_id=def_file_id,
            def_start=def_start,
            def_end=def_end,
            trust_level=trust_level,
            trust_source=trust_source,
            visibility=visibility,
            symbol_status=symbol_status,
            updated_at=now,
        )
        return self.db.insert_symbol(record)

    def _store_all_edges(
        self, parse_results: list[tuple[int, ParseResult]], all_symbols: list[SymbolRecord]
    ) -> None:
        """Phase 3: Store all edges using global symbol table."""
        # Build global symbol lookup tables
        name_to_ids: dict[str, list[int]] = {}
        qualified_to_id: dict[str, int] = {}

        for symbol in all_symbols:
            if symbol.id is None:
                continue
            qualified_to_id[symbol.qualified_name] = symbol.id
            if symbol.name not in name_to_ids:
                name_to_ids[symbol.name] = []
            name_to_ids[symbol.name].append(symbol.id)

        # Store edges for each file
        for file_id, result in parse_results:
            # Build import alias mapping for this file
            import_map = self._build_import_map(result)

            for edge in result.edges:
                src_id = qualified_to_id.get(edge.src_qualified_name)
                if not src_id:
                    continue

                # Try to resolve dst
                dst_id = self._resolve_edge_target(
                    edge.dst_qualified_name,
                    qualified_to_id,
                    name_to_ids,
                    import_map,
                )
                if dst_id:
                    record = EdgeRecord(
                        id=None,
                        src_symbol_id=src_id,
                        dst_symbol_id=dst_id,
                        edge_kind=edge.edge_kind,
                    )
                    self.db.insert_edge(record)

            # Store file edges
            self._store_file_edges(file_id, result)

    def _build_import_map(self, result: ParseResult) -> dict[str, str]:
        """Build mapping from short names to module paths based on imports."""
        import_map: dict[str, str] = {}
        module_path = result.file_path.replace("/", ".").replace(".py", "")

        for imp in result.imports:
            if imp.is_from_import:
                # from x.y import z -> z maps to x.y.z
                for name in imp.imported_names:
                    if imp.module_path.startswith("."):
                        # Relative import
                        base = ".".join(module_path.split(".")[:-1])
                        full_path = f"{base}{imp.module_path}.{name}"
                    else:
                        full_path = f"{imp.module_path}.{name}"
                    import_map[name] = full_path
            else:
                # import x.y -> x maps to x
                parts = imp.module_path.split(".")
                import_map[parts[0]] = imp.module_path

        return import_map

    def _resolve_edge_target(
        self,
        target: str,
        qualified_to_id: dict[str, int],
        name_to_ids: dict[str, list[int]],
        import_map: dict[str, str],
    ) -> Optional[int]:
        """Resolve edge target to symbol ID."""
        # 1. Try exact qualified name match
        if target in qualified_to_id:
            return qualified_to_id[target]

        # 2. Try to resolve via imports
        parts = target.split(".")
        if parts[0] in import_map:
            resolved = import_map[parts[0]]
            if len(parts) > 1:
                resolved = f"{resolved}.{'.'.join(parts[1:])}"
            if resolved in qualified_to_id:
                return qualified_to_id[resolved]

        # 3. Try short name match (prefer single match)
        short_name = parts[-1]
        if short_name in name_to_ids:
            ids = name_to_ids[short_name]
            if len(ids) == 1:
                return ids[0]

        return None

    def _get_parser(self, file_path: Path) -> Optional[BaseParser]:
        return self.parsers.get(file_path.suffix)

    def _store_file(self, file_path: Path, language: str, content_hash: str) -> int:
        existing = self.db.get_file_by_path(str(file_path))
        now = datetime.now().isoformat()

        in_workspace = self._is_in_workspace(file_path)

        if existing:
            existing.content_hash = content_hash
            existing.parse_status = "success"
            existing.last_indexed_at = now
            self.db.update_file(existing)
            return existing.id  # type: ignore

        record = FileRecord(
            id=None,
            path=str(file_path),
            parent_dir=str(file_path.parent),
            language=language,
            in_workspace=in_workspace,
            content_hash=content_hash,
            parse_status="success",
            last_indexed_at=now,
        )
        return self.db.insert_file(record)

    def _is_in_workspace(self, file_path: Path) -> bool:
        try:
            file_path.relative_to(self.config.workspace)
            return True
        except ValueError:
            return False

    def _clear_file_data(self, file_id: int) -> None:
        symbols = self.db.get_symbols_by_file_id(file_id)
        for symbol in symbols:
            if symbol.id:
                self.db.delete_edges_by_symbol_id(symbol.id)
                self.db.delete_namespace_members_by_symbol_id(symbol.id)
        self.db.delete_symbols_by_file_id(file_id)
        self.db.delete_file_edges_by_file_id(file_id)

    def _store_file_edges(self, file_id: int, result: ParseResult) -> None:
        src_file = self.db.get_file_by_id(file_id)
        if not src_file:
            return

        # Determine edge_kind based on language
        edge_kind = "include" if result.language == "cpp" else "import"

        for imp in result.imports:
            dst_path = self._resolve_import_path(
                imp.module_path, src_file.path, result.language
            )
            if dst_path:
                dst_file = self.db.get_file_by_path(str(dst_path))
                if dst_file:
                    record = FileEdgeRecord(
                        id=None,
                        src_file_id=file_id,
                        dst_file_id=dst_file.id,  # type: ignore
                        edge_kind=edge_kind,
                    )
                    self.db.insert_file_edge(record)

    def _resolve_import_path(
        self, module_path: str, src_file_path: str, language: str = "python"
    ) -> Optional[Path]:
        if language == "cpp":
            return self._resolve_cpp_include(module_path, src_file_path)

        # Handle Python relative imports
        if module_path.startswith("."):
            src_dir = Path(src_file_path).parent
            # Count leading dots
            dots = 0
            for c in module_path:
                if c == ".":
                    dots += 1
                else:
                    break

            # Go up directories based on dots
            base_dir = src_dir
            for _ in range(dots - 1):
                base_dir = base_dir.parent

            # Get the rest of the module path
            rest = module_path[dots:]
            if rest:
                parts = rest.split(".")
                candidates = [
                    base_dir / "/".join(parts) / "__init__.py",
                    base_dir / f"{'/'.join(parts)}.py",
                ]
            else:
                candidates = [base_dir / "__init__.py"]
        else:
            # Absolute import
            parts = module_path.split(".")
            candidates = [
                self.config.workspace / "/".join(parts) / "__init__.py",
                self.config.workspace / f"{'/'.join(parts)}.py",
            ]

        for candidate in candidates:
            # Resolve to normalize paths
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved
        return None

    def _resolve_cpp_include(
        self, include_path: str, src_file_path: str
    ) -> Optional[Path]:
        """Resolve C++ #include path.

        Handles relative paths like "../foo.h" and "dir/foo.h".
        """
        src_dir = Path(src_file_path).parent

        # Try relative to source file first
        candidates = [
            src_dir / include_path,
            self.config.workspace / include_path,
        ]

        # Also try common include directories
        for include_dir in ["include", "src", "."]:
            candidates.append(self.config.workspace / include_dir / include_path)

        for candidate in candidates:
            # Resolve to normalize paths like "../foo.h"
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved

        return None

    def _build_namespace_members(self, all_symbols: list[SymbolRecord]) -> None:
        """Build namespace_members table for C++ namespaces."""
        # Clear existing namespace data
        self.db.clear_namespaces()

        # Find and store namespace symbols
        namespace_qualified_to_id: dict[str, int] = {}

        for symbol in all_symbols:
            if symbol.symbol_kind == "namespace" and symbol.id is not None:
                # Check if parent namespace exists
                parent_ns_id = None
                if symbol.qualified_name and "::" in symbol.qualified_name:
                    parent_qn = "::".join(symbol.qualified_name.split("::")[:-1])
                    parent_ns_id = namespace_qualified_to_id.get(parent_qn)

                # Insert namespace
                ns_record = NamespaceRecord(
                    id=None,
                    name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    parent_namespace_id=parent_ns_id,
                )
                ns_id = self.db.insert_namespace(ns_record)
                namespace_qualified_to_id[symbol.qualified_name] = ns_id

        # Associate non-namespace symbols with their parent namespaces
        for symbol in all_symbols:
            if symbol.symbol_kind == "namespace" or symbol.id is None:
                continue

            # Find the namespace this symbol belongs to
            qn = symbol.qualified_name
            if "::" in qn:
                # Try to find the longest matching namespace prefix
                parts = qn.split("::")
                for i in range(len(parts) - 1, 0, -1):
                    potential_ns = "::".join(parts[:i])
                    if potential_ns in namespace_qualified_to_id:
                        ns_id = namespace_qualified_to_id[potential_ns]
                        self.db.insert_namespace_member(ns_id, symbol.id)
                        break
