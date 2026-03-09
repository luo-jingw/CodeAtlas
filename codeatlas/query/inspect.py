"""Inspect query for CodeAtlas - Level 1/2 symbol expansion."""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..storage.db import Database, SymbolRecord


@dataclass
class SymbolInfo:
    """Symbol information for inspection."""

    symbol_id: int
    name: str
    qualified_name: str
    symbol_kind: str
    file_path: str
    start_line: int
    end_line: int
    trust_level: str
    visibility: Optional[str]
    members: list["SymbolInfo"] = field(default_factory=list)


@dataclass
class ConnectionInfo:
    """Connection information between symbols."""

    src_name: str
    dst_qualified_name: str
    dst_file_path: str
    edge_kind: str


@dataclass
class SymbolConnections:
    """Symbol with its connections."""

    symbol: SymbolInfo
    connections: list[ConnectionInfo]


@dataclass
class InspectResult:
    """Result of inspect query."""

    target: str
    level: int
    symbols: list[SymbolInfo]
    connections: list[SymbolConnections]


def inspect(
    db: Database,
    workspace: Path,
    target: str,
    level: int = 1,
    max_items: int = 50,
    force_expand: bool = False,
    detail: str = "class",
) -> Optional[InspectResult]:
    """Inspect a module, namespace, or class.

    Args:
        db: Database connection
        workspace: Workspace root path
        target: Target path, namespace, or #id
        level: Expansion level (1 or 2)
        max_items: Maximum symbols to return
        force_expand: Override high trust folding
        detail: "class" for class-level aggregation, "method" for method-level

    Returns:
        InspectResult with symbols and connections
    """
    if target.startswith("#"):
        return _inspect_by_id(db, workspace, target, level, max_items, force_expand, detail)

    return _inspect_by_path(db, workspace, target, level, max_items, force_expand, detail)


def _inspect_by_id(
    db: Database,
    workspace: Path,
    target: str,
    level: int,
    max_items: int,
    force_expand: bool,
    detail: str,
) -> Optional[InspectResult]:
    """Inspect by symbol ID."""
    try:
        symbol_id = int(target[1:])
    except ValueError:
        return None

    symbol = db.get_symbol_by_id(symbol_id)
    if not symbol:
        return None

    file_record = db.get_file_by_id(symbol.file_id)
    if not file_record:
        return None

    symbol_info = _to_symbol_info(symbol, file_record.path)

    if symbol.symbol_kind == "class":
        symbol_info.members = _get_class_members(db, symbol_id)

    symbols = [symbol_info]
    connections: list[SymbolConnections] = []

    if level >= 2:
        connections = _get_connections(db, workspace, [symbol], detail)

    return InspectResult(
        target=target,
        level=level,
        symbols=symbols,
        connections=connections,
    )


def _inspect_by_path(
    db: Database,
    workspace: Path,
    target: str,
    level: int,
    max_items: int,
    force_expand: bool,
    detail: str,
) -> Optional[InspectResult]:
    """Inspect by file/directory path or qualified name."""
    all_files = db.get_all_files()
    all_symbols = db.get_all_symbols()

    matching_files = [
        f for f in all_files
        if target in f.path or f.path.endswith(target) or f.parent_dir.endswith(target)
    ]

    if matching_files:
        file_ids = {f.id for f in matching_files}
        matching_symbols = [
            s for s in all_symbols
            if s.file_id in file_ids and s.parent_symbol_id is None
        ]
    else:
        matching_symbols = [
            s for s in all_symbols
            if s.qualified_name.startswith(target) or target in s.qualified_name
        ]

    if not matching_symbols:
        return None

    files = {f.id: f for f in all_files}
    symbols: list[SymbolInfo] = []

    for symbol in matching_symbols[:max_items]:
        if not force_expand and symbol.trust_level == "high":
            continue

        file_record = files.get(symbol.file_id)
        if not file_record:
            continue

        symbol_info = _to_symbol_info(symbol, file_record.path)

        if symbol.symbol_kind == "class":
            symbol_info.members = _get_class_members(db, symbol.id)  # type: ignore

        symbols.append(symbol_info)

    connections: list[SymbolConnections] = []
    if level >= 2:
        connections = _get_connections(db, workspace, matching_symbols[:max_items], detail)

    return InspectResult(
        target=target,
        level=level,
        symbols=symbols,
        connections=connections,
    )


def _to_symbol_info(symbol: SymbolRecord, file_path: str) -> SymbolInfo:
    """Convert SymbolRecord to SymbolInfo."""
    return SymbolInfo(
        symbol_id=symbol.id,  # type: ignore
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        symbol_kind=symbol.symbol_kind,
        file_path=file_path,
        start_line=symbol.def_start,
        end_line=symbol.def_end,
        trust_level=symbol.trust_level,
        visibility=symbol.visibility,
    )


def _get_class_members(db: Database, class_id: int) -> list[SymbolInfo]:
    """Get members of a class."""
    all_symbols = db.get_all_symbols()
    files = {f.id: f for f in db.get_all_files()}

    members = [
        s for s in all_symbols
        if s.parent_symbol_id == class_id
    ]

    result: list[SymbolInfo] = []
    for member in members:
        file_record = files.get(member.file_id)
        if file_record:
            result.append(_to_symbol_info(member, file_record.path))

    return result


def _get_connections(
    db: Database,
    workspace: Path,
    symbols: list[SymbolRecord],
    detail: str,
) -> list[SymbolConnections]:
    """Get connections for symbols."""
    all_edges = db.get_all_edges()
    all_symbols = {s.id: s for s in db.get_all_symbols()}
    all_files = {f.id: f for f in db.get_all_files()}

    symbol_ids = {s.id for s in symbols}
    result: list[SymbolConnections] = []

    for symbol in symbols:
        if symbol.id is None:
            continue

        edges = [e for e in all_edges if e.src_symbol_id == symbol.id]

        file_record = all_files.get(symbol.file_id)
        if not file_record:
            continue

        symbol_info = _to_symbol_info(symbol, file_record.path)
        connections: list[ConnectionInfo] = []

        if detail == "class" and symbol.symbol_kind == "class":
            seen_targets: set[str] = set()
            for edge in edges:
                dst_symbol = all_symbols.get(edge.dst_symbol_id)
                if not dst_symbol:
                    continue

                dst_class = _get_containing_class(all_symbols, dst_symbol)
                target_name = dst_class.qualified_name if dst_class else dst_symbol.qualified_name

                if target_name in seen_targets:
                    continue
                seen_targets.add(target_name)

                dst_file = all_files.get(
                    dst_class.file_id if dst_class else dst_symbol.file_id
                )
                connections.append(
                    ConnectionInfo(
                        src_name=symbol.name,
                        dst_qualified_name=target_name,
                        dst_file_path=dst_file.path if dst_file else "",
                        edge_kind=edge.edge_kind,
                    )
                )
        else:
            for edge in edges:
                dst_symbol = all_symbols.get(edge.dst_symbol_id)
                if not dst_symbol:
                    continue

                dst_file = all_files.get(dst_symbol.file_id)
                connections.append(
                    ConnectionInfo(
                        src_name=symbol.name,
                        dst_qualified_name=dst_symbol.qualified_name,
                        dst_file_path=dst_file.path if dst_file else "",
                        edge_kind=edge.edge_kind,
                    )
                )

        if connections:
            result.append(SymbolConnections(symbol=symbol_info, connections=connections))

    return result


def _get_containing_class(
    symbols: dict[int, SymbolRecord],
    symbol: SymbolRecord,
) -> Optional[SymbolRecord]:
    """Get the containing class of a symbol."""
    if symbol.symbol_kind == "class":
        return symbol

    if symbol.parent_symbol_id:
        parent = symbols.get(symbol.parent_symbol_id)
        if parent and parent.symbol_kind == "class":
            return parent

    return None
