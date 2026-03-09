"""Python language parser using Tree-sitter."""

import hashlib
from pathlib import Path
from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node

from .base import BaseParser, EdgeInfo, ImportInfo, ParseResult, SymbolInfo


class PythonParser(BaseParser):
    """Parser for Python source files using Tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(Language(tspython.language()))

    @property
    def language(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return [".py", ".pyi"]

    def parse(self, file_path: Path, source: str) -> ParseResult:
        tree = self._parser.parse(source.encode("utf-8"))
        module_path = self._compute_module_path(file_path)

        symbols: list[SymbolInfo] = []
        edges: list[EdgeInfo] = []
        imports: list[ImportInfo] = []
        errors: list[str] = []

        self._extract_symbols(
            tree.root_node,
            module_path,
            None,
            symbols,
            edges,
            imports,
            errors,
            source,
        )

        return ParseResult(
            file_path=str(file_path),
            language=self.language,
            symbols=symbols,
            edges=edges,
            imports=imports,
            errors=errors,
        )

    def compute_signature_hash(
        self,
        language: str,
        qualified_name: str,
        symbol_kind: str,
        arity: Optional[int],
    ) -> str:
        parts = qualified_name.rsplit(".", 1)
        if len(parts) == 2:
            container, name = parts
        else:
            container = ""
            name = parts[0]

        hash_input = f"{language}:{container}:{symbol_kind}:{name}:{arity if arity is not None else ''}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _compute_module_path(self, file_path: Path) -> str:
        parts = list(file_path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _extract_symbols(
        self,
        node: Node,
        module_path: str,
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        imports: list[ImportInfo],
        errors: list[str],
        source: str,
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                self._extract_function(
                    child,
                    module_path,
                    parent_qualified_name,
                    symbols,
                    edges,
                    source,
                )
            elif child.type == "decorated_definition":
                # Handle decorated functions/classes
                for subchild in child.children:
                    if subchild.type == "function_definition":
                        self._extract_function(
                            subchild,
                            module_path,
                            parent_qualified_name,
                            symbols,
                            edges,
                            source,
                        )
                    elif subchild.type == "class_definition":
                        self._extract_class(
                            subchild,
                            module_path,
                            parent_qualified_name,
                            symbols,
                            edges,
                            imports,
                            errors,
                            source,
                        )
            elif child.type == "class_definition":
                self._extract_class(
                    child,
                    module_path,
                    parent_qualified_name,
                    symbols,
                    edges,
                    imports,
                    errors,
                    source,
                )
            elif child.type == "expression_statement":
                self._extract_global_variable(
                    child,
                    module_path,
                    parent_qualified_name,
                    symbols,
                    source,
                )
            elif child.type == "import_statement":
                self._extract_import(child, imports, source)
            elif child.type == "import_from_statement":
                self._extract_from_import(child, imports, source)

    def _extract_function(
        self,
        node: Node,
        module_path: str,
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        name = self._get_node_text(name_node, source)
        if parent_qualified_name:
            qualified_name = f"{parent_qualified_name}.{name}"
            symbol_kind = "method"
        else:
            qualified_name = f"{module_path}.{name}"
            symbol_kind = "function"

        params_node = node.child_by_field_name("parameters")
        arity = self._count_parameters(params_node, source) if params_node else 0

        visibility = self._determine_visibility(name, module_path)

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, symbol_kind, arity
            ),
            symbol_kind=symbol_kind,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent_qualified_name,
            visibility=visibility,
            arity=arity,
        )
        symbols.append(symbol)

        body_node = node.child_by_field_name("body")
        if body_node:
            self._extract_edges_from_body(body_node, qualified_name, edges, source)

    def _extract_class(
        self,
        node: Node,
        module_path: str,
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        imports: list[ImportInfo],
        errors: list[str],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        name = self._get_node_text(name_node, source)
        if parent_qualified_name:
            qualified_name = f"{parent_qualified_name}.{name}"
        else:
            qualified_name = f"{module_path}.{name}"

        visibility = self._determine_visibility(name, module_path)

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, "class", None
            ),
            symbol_kind="class",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent_qualified_name,
            visibility=visibility,
            arity=None,
        )
        symbols.append(symbol)

        superclass_node = node.child_by_field_name("superclasses")
        if superclass_node:
            self._extract_inheritance_edges(
                superclass_node, qualified_name, edges, source
            )

        body_node = node.child_by_field_name("body")
        if body_node:
            self._extract_symbols(
                body_node,
                module_path,
                qualified_name,
                symbols,
                edges,
                imports,
                errors,
                source,
            )

    def _extract_global_variable(
        self,
        node: Node,
        module_path: str,
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        source: str,
    ) -> None:
        if parent_qualified_name:
            return

        for child in node.children:
            if child.type == "assignment":
                left = child.child_by_field_name("left")
                if left and left.type == "identifier":
                    name = self._get_node_text(left, source)
                    if name.isupper():
                        qualified_name = f"{module_path}.{name}"
                        visibility = self._determine_visibility(name, module_path)

                        symbol = SymbolInfo(
                            name=name,
                            qualified_name=qualified_name,
                            signature_hash=self.compute_signature_hash(
                                self.language, qualified_name, "variable", None
                            ),
                            symbol_kind="variable",
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            parent_qualified_name=None,
                            visibility=visibility,
                            arity=None,
                        )
                        symbols.append(symbol)

    def _extract_import(
        self,
        node: Node,
        imports: list[ImportInfo],
        source: str,
    ) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                module_name = self._get_node_text(child, source)
                imports.append(
                    ImportInfo(
                        module_path=module_name,
                        imported_names=[],
                        is_from_import=False,
                    )
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    module_name = self._get_node_text(name_node, source)
                    imports.append(
                        ImportInfo(
                            module_path=module_name,
                            imported_names=[],
                            is_from_import=False,
                        )
                    )

    def _extract_from_import(
        self,
        node: Node,
        imports: list[ImportInfo],
        source: str,
    ) -> None:
        module_node = node.child_by_field_name("module_name")
        if not module_node:
            return

        module_name = self._get_node_text(module_node, source)
        imported_names: list[str] = []

        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                imported_names.append(self._get_node_text(child, source))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node:
                    imported_names.append(self._get_node_text(name_node, source))

        imports.append(
            ImportInfo(
                module_path=module_name,
                imported_names=imported_names,
                is_from_import=True,
            )
        )

    def _extract_edges_from_body(
        self,
        body_node: Node,
        src_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        # Track names that are already recorded as calls to avoid duplicates
        call_targets: set[str] = set()

        calls = self._find_nodes_by_type(body_node, "call")
        for call in calls:
            func_node = call.child_by_field_name("function")
            if func_node:
                dst_name = self._get_call_target(func_node, source)
                if dst_name:
                    call_targets.add(dst_name)
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=src_qualified_name,
                            dst_qualified_name=dst_name,
                            edge_kind="call",
                        )
                    )

        type_annotations = self._find_type_annotations(body_node)
        type_names: set[str] = set()
        for ann in type_annotations:
            type_name = self._get_node_text(ann, source)
            if type_name and not self._is_builtin_type(type_name):
                type_names.add(type_name)
                edges.append(
                    EdgeInfo(
                        src_qualified_name=src_qualified_name,
                        dst_qualified_name=type_name,
                        edge_kind="use_type",
                    )
                )

        # Extract references to identifiers/attributes (not calls or types)
        self._extract_references(
            body_node, src_qualified_name, edges, source, call_targets, type_names
        )

    def _extract_references(
        self,
        body_node: Node,
        src_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
        call_targets: set[str],
        type_names: set[str],
    ) -> None:
        """Extract reference edges for identifiers/attributes that aren't calls or types.

        This captures usages like accessing global variables, constants, or module attributes.
        """
        seen_refs: set[str] = set()

        # Find all identifier usages that might be references
        identifiers = self._find_nodes_by_type(body_node, "identifier")
        for ident in identifiers:
            name = self._get_node_text(ident, source)
            if not name or name in seen_refs:
                continue
            # Skip if it's a call target, type, or local variable pattern
            if name in call_targets or name in type_names:
                continue
            # Skip common Python built-ins and keywords
            if self._is_python_builtin(name):
                continue
            # Skip 'self' and 'cls'
            if name in ("self", "cls"):
                continue
            # Skip if it's a parameter or local definition (simple heuristic: starts lower)
            # Only track UPPER_CASE names (likely constants) or CamelCase (likely class refs)
            if not (name.isupper() or (name[0].isupper() and not name.isupper())):
                continue

            # Check parent to avoid function definitions, assignments left-hand side
            parent = ident.parent
            if parent:
                if parent.type in ("function_definition", "class_definition"):
                    continue
                if parent.type == "assignment" and parent.child_by_field_name("left") == ident:
                    continue

            seen_refs.add(name)
            edges.append(
                EdgeInfo(
                    src_qualified_name=src_qualified_name,
                    dst_qualified_name=name,
                    edge_kind="reference",
                )
            )

        # Find attribute accesses (e.g., module.CONSTANT, obj.attribute)
        attributes = self._find_nodes_by_type(body_node, "attribute")
        for attr in attributes:
            # Skip if this attribute is a call target
            attr_text = self._get_node_text(attr, source)
            if attr_text in call_targets or attr_text in type_names:
                continue
            if attr_text in seen_refs:
                continue

            # Only track if the final attribute name is UPPER_CASE (constant-like)
            attr_name_node = attr.child_by_field_name("attribute")
            if attr_name_node:
                attr_name = self._get_node_text(attr_name_node, source)
                if attr_name.isupper():
                    seen_refs.add(attr_text)
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=src_qualified_name,
                            dst_qualified_name=attr_text,
                            edge_kind="reference",
                        )
                    )

    def _is_python_builtin(self, name: str) -> bool:
        """Check if name is a Python built-in."""
        builtins = {
            "True", "False", "None", "print", "len", "range", "str", "int",
            "float", "list", "dict", "set", "tuple", "type", "isinstance",
            "issubclass", "hasattr", "getattr", "setattr", "delattr",
            "open", "input", "super", "property", "staticmethod", "classmethod",
            "enumerate", "zip", "map", "filter", "sorted", "reversed",
            "min", "max", "sum", "abs", "round", "pow", "divmod",
            "id", "hash", "repr", "format", "vars", "dir", "help",
            "iter", "next", "slice", "object", "Exception", "BaseException",
        }
        return name in builtins

    def _extract_inheritance_edges(
        self,
        superclass_node: Node,
        class_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        for child in superclass_node.children:
            if child.type in ("identifier", "attribute"):
                base_name = self._get_node_text(child, source)
                if base_name and not self._is_builtin_type(base_name):
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=class_qualified_name,
                            dst_qualified_name=base_name,
                            edge_kind="inherit",
                        )
                    )

    def _get_call_target(self, node: Node, source: str) -> Optional[str]:
        if node.type == "identifier":
            return self._get_node_text(node, source)
        elif node.type == "attribute":
            return self._get_node_text(node, source)
        return None

    def _find_nodes_by_type(self, node: Node, node_type: str) -> list[Node]:
        results: list[Node] = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes_by_type(child, node_type))
        return results

    def _find_type_annotations(self, node: Node) -> list[Node]:
        results: list[Node] = []
        for child in node.children:
            if child.type == "type":
                results.append(child)
            results.extend(self._find_type_annotations(child))
        return results

    def _count_parameters(self, params_node: Node, source: str) -> int:
        count = 0
        for child in params_node.children:
            if child.type in ("identifier", "typed_parameter", "default_parameter"):
                name = self._get_param_name(child, source)
                if name and name != "self" and name != "cls":
                    count += 1
            elif child.type == "list_splat_pattern":
                count += 1
            elif child.type == "dictionary_splat_pattern":
                count += 1
        return count

    def _get_param_name(self, node: Node, source: str) -> Optional[str]:
        if node.type == "identifier":
            return self._get_node_text(node, source)
        elif node.type == "typed_parameter":
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._get_node_text(name_node, source)
        elif node.type == "default_parameter":
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._get_node_text(name_node, source)
        return None

    def _get_node_text(self, node: Node, source: str) -> str:
        return source[node.start_byte : node.end_byte]

    def _determine_visibility(self, name: str, module_path: str) -> Optional[str]:
        if name.startswith("_") and not name.startswith("__"):
            return "private"
        if "test" in module_path.lower() or name.startswith("test_"):
            return "test"
        return None

    def _is_builtin_type(self, type_name: str) -> bool:
        builtins = {
            "int",
            "str",
            "float",
            "bool",
            "list",
            "dict",
            "set",
            "tuple",
            "None",
            "Any",
            "Optional",
            "List",
            "Dict",
            "Set",
            "Tuple",
            "Union",
            "Callable",
            "Type",
            "Sequence",
            "Iterable",
            "Iterator",
            "Generator",
            "Mapping",
            "MutableMapping",
        }
        base_type = type_name.split("[")[0].split(".")[-1]
        return base_type in builtins
