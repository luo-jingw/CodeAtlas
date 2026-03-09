"""C/C++ language parser using Tree-sitter."""

import hashlib
from pathlib import Path
from typing import Optional

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Node

from .base import BaseParser, EdgeInfo, ImportInfo, ParseResult, SymbolInfo


class CppParser(BaseParser):
    """Parser for C/C++ source files using Tree-sitter."""

    def __init__(self) -> None:
        self._parser = Parser(Language(tscpp.language()))

    @property
    def language(self) -> str:
        return "cpp"

    @property
    def file_extensions(self) -> list[str]:
        return [".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"]

    def parse(self, file_path: Path, source: str) -> ParseResult:
        tree = self._parser.parse(source.encode("utf-8"))

        symbols: list[SymbolInfo] = []
        edges: list[EdgeInfo] = []
        imports: list[ImportInfo] = []
        errors: list[str] = []

        self._extract_symbols(
            tree.root_node,
            [],  # namespace_stack
            None,  # parent_qualified_name
            None,  # current_visibility
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
        parameter_types: Optional[list[str]] = None,
    ) -> str:
        type_list = ",".join(parameter_types) if parameter_types else ""
        hash_input = f"{language}:{qualified_name}:{symbol_kind}:{type_list}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    def _extract_symbols(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        current_visibility: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        imports: list[ImportInfo],
        errors: list[str],
        source: str,
    ) -> None:
        for child in node.children:
            # Handle preprocessor directives (recurse into their content)
            if child.type in ("preproc_ifdef", "preproc_if", "preproc_elif", "preproc_else"):
                self._extract_symbols(
                    child, namespace_stack, parent_qualified_name,
                    current_visibility, symbols, edges, imports, errors, source
                )
            elif child.type == "namespace_definition":
                self._extract_namespace(
                    child, namespace_stack, symbols, edges, imports, errors, source
                )
            elif child.type == "class_specifier":
                self._extract_class(
                    child, namespace_stack, parent_qualified_name,
                    "class", symbols, edges, source
                )
            elif child.type == "struct_specifier":
                self._extract_class(
                    child, namespace_stack, parent_qualified_name,
                    "struct", symbols, edges, source
                )
            elif child.type == "enum_specifier":
                self._extract_enum(
                    child, namespace_stack, parent_qualified_name, symbols, source
                )
            elif child.type == "function_definition":
                self._extract_function(
                    child, namespace_stack, parent_qualified_name,
                    current_visibility, symbols, edges, source
                )
            elif child.type == "declaration":
                self._extract_declaration(
                    child, namespace_stack, parent_qualified_name,
                    current_visibility, symbols, edges, source
                )
            elif child.type == "preproc_include":
                self._extract_include(child, imports, source)
            elif child.type == "type_alias_declaration":
                self._extract_type_alias(
                    child, namespace_stack, parent_qualified_name, symbols, source
                )
            elif child.type == "using_declaration":
                self._extract_using(
                    child, namespace_stack, parent_qualified_name, symbols, source
                )
            elif child.type == "template_declaration":
                self._extract_template(
                    child, namespace_stack, parent_qualified_name,
                    current_visibility, symbols, edges, imports, errors, source
                )

    def _extract_namespace(
        self,
        node: Node,
        namespace_stack: list[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        imports: list[ImportInfo],
        errors: list[str],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            name = self._get_node_text(name_node, source)
        else:
            name = "<anonymous>"

        new_stack = namespace_stack + [name]
        qualified_name = "::".join(new_stack)

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, "namespace", None
            ),
            symbol_kind="namespace",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name="::".join(namespace_stack) if namespace_stack else None,
            visibility=None,
            arity=None,
        )
        symbols.append(symbol)

        body_node = node.child_by_field_name("body")
        if body_node:
            self._extract_symbols(
                body_node, new_stack, qualified_name, None,
                symbols, edges, imports, errors, source
            )

    def _extract_class(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        symbol_kind: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        # Check if this is a forward declaration (no body)
        body_node = node.child_by_field_name("body")
        is_forward_declaration = body_node is None

        # Skip forward declarations - they will be linked to actual definitions
        # via signature_hash if a definition exists
        if is_forward_declaration:
            return

        name = self._get_node_text(name_node, source)
        if parent_qualified_name:
            qualified_name = f"{parent_qualified_name}::{name}"
        elif namespace_stack:
            qualified_name = f"{'::'.join(namespace_stack)}::{name}"
        else:
            qualified_name = name

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, symbol_kind, None
            ),
            symbol_kind=symbol_kind,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent_qualified_name,
            visibility=None,
            arity=None,
        )
        symbols.append(symbol)

        # Extract base classes (inheritance)
        for child in node.children:
            if child.type == "base_class_clause":
                self._extract_inheritance(child, qualified_name, edges, source)

        # Extract class body (handles both declaration_list and field_declaration_list)
        # Default visibility: private for class, public for struct
        default_visibility = "private" if symbol_kind == "class" else "public"
        self._extract_class_body(
            body_node, namespace_stack, qualified_name,
            default_visibility, symbols, edges, source
        )

    def _extract_class_body(
        self,
        body_node: Node,
        namespace_stack: list[str],
        class_qualified_name: str,
        current_visibility: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        visibility = current_visibility

        for child in body_node.children:
            if child.type == "access_specifier":
                visibility = self._get_access_specifier(child, source)
            elif child.type == "function_definition":
                self._extract_method(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )
            elif child.type == "declaration":
                self._extract_member_declaration(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )
            elif child.type == "field_declaration":
                # Method declarations in header files
                self._extract_field_declaration(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )
            elif child.type == "class_specifier":
                self._extract_class(
                    child, namespace_stack, class_qualified_name,
                    "class", symbols, edges, source
                )
            elif child.type == "struct_specifier":
                self._extract_class(
                    child, namespace_stack, class_qualified_name,
                    "struct", symbols, edges, source
                )
            elif child.type == "template_declaration":
                self._extract_template_in_class(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )

    def _extract_method(
        self,
        node: Node,
        namespace_stack: list[str],
        class_qualified_name: str,
        visibility: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return

        name, param_types = self._parse_function_declarator(declarator, source)
        if not name:
            return

        qualified_name = f"{class_qualified_name}::{name}"
        arity = len(param_types)

        # Check if this is a definition (has body) or declaration
        body_node = node.child_by_field_name("body")
        is_definition = body_node is not None

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, "method", arity, param_types
            ),
            symbol_kind="method",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=class_qualified_name,
            visibility=visibility,
            arity=arity,
            is_definition=is_definition,
        )
        symbols.append(symbol)

        # Extract edges from function body
        if body_node:
            self._extract_edges_from_body(body_node, qualified_name, edges, source)

    def _extract_member_declaration(
        self,
        node: Node,
        namespace_stack: list[str],
        class_qualified_name: str,
        visibility: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        # Check if it's a method declaration (no body = declaration only)
        for child in node.children:
            if child.type == "function_declarator":
                name, param_types = self._parse_function_declarator(child, source)
                if name:
                    qualified_name = f"{class_qualified_name}::{name}"
                    arity = len(param_types)

                    symbol = SymbolInfo(
                        name=name,
                        qualified_name=qualified_name,
                        signature_hash=self.compute_signature_hash(
                            self.language, qualified_name, "method", arity, param_types
                        ),
                        symbol_kind="method",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_qualified_name=class_qualified_name,
                        visibility=visibility,
                        arity=arity,
                        is_definition=False,  # Declaration only
                    )
                    symbols.append(symbol)
                return

    def _extract_field_declaration(
        self,
        node: Node,
        namespace_stack: list[str],
        class_qualified_name: str,
        visibility: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        """Extract method declarations from field_declaration in header files."""
        # Look for function declarator in field declaration
        declarator = node.child_by_field_name("declarator")
        if declarator and declarator.type == "function_declarator":
            name, param_types = self._parse_function_declarator(declarator, source)
            if name:
                qualified_name = f"{class_qualified_name}::{name}"
                arity = len(param_types)

                symbol = SymbolInfo(
                    name=name,
                    qualified_name=qualified_name,
                    signature_hash=self.compute_signature_hash(
                        self.language, qualified_name, "method", arity, param_types
                    ),
                    symbol_kind="method",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_qualified_name=class_qualified_name,
                    visibility=visibility,
                    arity=arity,
                    is_definition=False,  # Declaration only
                )
                symbols.append(symbol)

    def _extract_function(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        visibility: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return

        name, param_types = self._parse_function_declarator(declarator, source)
        if not name:
            return

        # Check if this is a method definition outside class (e.g., Class::method)
        if "::" in name:
            # Method definition outside class - prepend namespace if present
            if namespace_stack:
                # Check if name already starts with namespace prefix
                ns_prefix = "::".join(namespace_stack) + "::"
                if not name.startswith(ns_prefix):
                    qualified_name = f"{ns_prefix}{name}"
                else:
                    qualified_name = name
            else:
                qualified_name = name
            simple_name = name.split("::")[-1]
            symbol_kind = "method"
            # Parent is the class, with namespace prefix if present
            class_name = "::".join(name.split("::")[:-1])
            if namespace_stack and not class_name.startswith("::".join(namespace_stack)):
                parent = f"{'::'.join(namespace_stack)}::{class_name}"
            else:
                parent = class_name
        else:
            if namespace_stack:
                qualified_name = f"{'::'.join(namespace_stack)}::{name}"
            else:
                qualified_name = name
            simple_name = name
            symbol_kind = "function"
            parent = parent_qualified_name

        arity = len(param_types)

        # Check if this is a definition (has body)
        body_node = node.child_by_field_name("body")
        is_definition = body_node is not None

        symbol = SymbolInfo(
            name=simple_name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, symbol_kind, arity, param_types
            ),
            symbol_kind=symbol_kind,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent,
            visibility=visibility,
            arity=arity,
            is_definition=is_definition,
        )
        symbols.append(symbol)

        # Extract edges from function body
        if body_node:
            self._extract_edges_from_body(body_node, qualified_name, edges, source)

    def _extract_declaration(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        visibility: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        # Check for function declaration (no body = declaration only)
        for child in node.children:
            if child.type == "function_declarator":
                name, param_types = self._parse_function_declarator(child, source)
                if name:
                    if namespace_stack:
                        qualified_name = f"{'::'.join(namespace_stack)}::{name}"
                    else:
                        qualified_name = name

                    arity = len(param_types)
                    symbol = SymbolInfo(
                        name=name,
                        qualified_name=qualified_name,
                        signature_hash=self.compute_signature_hash(
                            self.language, qualified_name, "function", arity, param_types
                        ),
                        symbol_kind="function",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_qualified_name=parent_qualified_name,
                        visibility=visibility,
                        arity=arity,
                        is_definition=False,  # Declaration only
                    )
                    symbols.append(symbol)
                return

        # Check for global variable (simple declaration with init)
        if parent_qualified_name is None:
            self._extract_global_variable(node, namespace_stack, symbols, source)

    def _extract_global_variable(
        self,
        node: Node,
        namespace_stack: list[str],
        symbols: list[SymbolInfo],
        source: str,
    ) -> None:
        """Extract global variables with simple assignments.

        Only extracts variables with initializers (init_declarator).
        Skips function pointers and complex declarations.
        """
        for child in node.children:
            if child.type == "init_declarator":
                declarator = child.child_by_field_name("declarator")
                if not declarator:
                    continue

                # Handle simple identifier
                if declarator.type == "identifier":
                    name = self._get_node_text(declarator, source)
                # Handle pointer declarator (e.g., int* ptr = ...)
                elif declarator.type == "pointer_declarator":
                    inner = self._get_innermost_declarator(declarator)
                    if inner and inner.type == "identifier":
                        name = self._get_node_text(inner, source)
                    else:
                        continue
                else:
                    continue

                # Skip names starting with _ (internal/reserved)
                if name.startswith("_"):
                    continue

                if namespace_stack:
                    qualified_name = f"{'::'.join(namespace_stack)}::{name}"
                else:
                    qualified_name = name

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
                    visibility=None,
                    arity=None,
                )
                symbols.append(symbol)

    def _get_innermost_declarator(self, node: Node) -> Optional[Node]:
        """Get the innermost declarator from nested pointer/reference declarators."""
        if node.type == "identifier":
            return node
        for child in node.children:
            if child.type in ("identifier", "pointer_declarator", "reference_declarator"):
                return self._get_innermost_declarator(child)
        return None

    def _extract_enum(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        name = self._get_node_text(name_node, source)
        if parent_qualified_name:
            qualified_name = f"{parent_qualified_name}::{name}"
        elif namespace_stack:
            qualified_name = f"{'::'.join(namespace_stack)}::{name}"
        else:
            qualified_name = name

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, "enum", None
            ),
            symbol_kind="enum",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent_qualified_name,
            visibility=None,
            arity=None,
        )
        symbols.append(symbol)

    def _extract_type_alias(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        source: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        name = self._get_node_text(name_node, source)
        if parent_qualified_name:
            qualified_name = f"{parent_qualified_name}::{name}"
        elif namespace_stack:
            qualified_name = f"{'::'.join(namespace_stack)}::{name}"
        else:
            qualified_name = name

        symbol = SymbolInfo(
            name=name,
            qualified_name=qualified_name,
            signature_hash=self.compute_signature_hash(
                self.language, qualified_name, "alias", None
            ),
            symbol_kind="alias",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_qualified_name=parent_qualified_name,
            visibility=None,
            arity=None,
        )
        symbols.append(symbol)

    def _extract_using(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        symbols: list[SymbolInfo],
        source: str,
    ) -> None:
        # using Type = OtherType;
        for child in node.children:
            if child.type == "type_identifier":
                name = self._get_node_text(child, source)
                if parent_qualified_name:
                    qualified_name = f"{parent_qualified_name}::{name}"
                elif namespace_stack:
                    qualified_name = f"{'::'.join(namespace_stack)}::{name}"
                else:
                    qualified_name = name

                symbol = SymbolInfo(
                    name=name,
                    qualified_name=qualified_name,
                    signature_hash=self.compute_signature_hash(
                        self.language, qualified_name, "alias", None
                    ),
                    symbol_kind="alias",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_qualified_name=parent_qualified_name,
                    visibility=None,
                    arity=None,
                )
                symbols.append(symbol)
                break

    def _extract_template(
        self,
        node: Node,
        namespace_stack: list[str],
        parent_qualified_name: Optional[str],
        visibility: Optional[str],
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        imports: list[ImportInfo],
        errors: list[str],
        source: str,
    ) -> None:
        # Process the templated declaration
        for child in node.children:
            if child.type == "function_definition":
                self._extract_function(
                    child, namespace_stack, parent_qualified_name,
                    visibility, symbols, edges, source
                )
            elif child.type == "class_specifier":
                self._extract_class(
                    child, namespace_stack, parent_qualified_name,
                    "class", symbols, edges, source
                )
            elif child.type == "struct_specifier":
                self._extract_class(
                    child, namespace_stack, parent_qualified_name,
                    "struct", symbols, edges, source
                )
            elif child.type == "declaration":
                self._extract_declaration(
                    child, namespace_stack, parent_qualified_name,
                    visibility, symbols, edges, source
                )

    def _extract_template_in_class(
        self,
        node: Node,
        namespace_stack: list[str],
        class_qualified_name: str,
        visibility: str,
        symbols: list[SymbolInfo],
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        for child in node.children:
            if child.type == "function_definition":
                self._extract_method(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )
            elif child.type == "declaration":
                self._extract_member_declaration(
                    child, namespace_stack, class_qualified_name,
                    visibility, symbols, edges, source
                )

    def _extract_include(
        self,
        node: Node,
        imports: list[ImportInfo],
        source: str,
    ) -> None:
        path_node = node.child_by_field_name("path")
        if path_node:
            path_text = self._get_node_text(path_node, source)
            # Remove quotes or angle brackets
            if path_text.startswith('"') and path_text.endswith('"'):
                path = path_text[1:-1]
                is_system = False
            elif path_text.startswith("<") and path_text.endswith(">"):
                path = path_text[1:-1]
                is_system = True
            else:
                path = path_text
                is_system = False

            # Only record non-system includes
            if not is_system:
                imports.append(
                    ImportInfo(
                        module_path=path,
                        imported_names=[],
                        is_from_import=False,
                    )
                )

    def _extract_inheritance(
        self,
        base_clause: Node,
        class_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        for child in base_clause.children:
            if child.type == "base_class_specifier":
                type_node = None
                for subchild in child.children:
                    if subchild.type in ("type_identifier", "qualified_identifier", "template_type"):
                        type_node = subchild
                        break

                if type_node:
                    base_name = self._get_node_text(type_node, source)
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=class_qualified_name,
                            dst_qualified_name=base_name,
                            edge_kind="inherit",
                        )
                    )

    def _extract_edges_from_body(
        self,
        body_node: Node,
        src_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
    ) -> None:
        # Track names already recorded to avoid duplicates
        call_targets: set[str] = set()

        # Extract function calls
        calls = self._find_nodes_by_type(body_node, "call_expression")
        for call in calls:
            func_node = call.child_by_field_name("function")
            if func_node:
                func_name = self._get_node_text(func_node, source)
                if func_name and not self._is_builtin(func_name):
                    call_targets.add(func_name)
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=src_qualified_name,
                            dst_qualified_name=func_name,
                            edge_kind="call",
                        )
                    )

        # Extract template instantiations (e.g., vector<int>, make_shared<T>())
        template_types = self._find_nodes_by_type(body_node, "template_type")
        seen_templates: set[str] = set()
        for template_type in template_types:
            template_name = self._get_template_name(template_type, source)
            if template_name and template_name not in seen_templates:
                if not self._is_std_template(template_name):
                    seen_templates.add(template_name)
                    edges.append(
                        EdgeInfo(
                            src_qualified_name=src_qualified_name,
                            dst_qualified_name=template_name,
                            edge_kind="instantiate",
                        )
                    )

        # Extract type usage
        type_nodes = self._find_type_identifiers(body_node)
        seen_types: set[str] = set()
        for type_node in type_nodes:
            type_name = self._get_node_text(type_node, source)
            if type_name and type_name not in seen_types and not self._is_builtin_type(type_name):
                seen_types.add(type_name)
                edges.append(
                    EdgeInfo(
                        src_qualified_name=src_qualified_name,
                        dst_qualified_name=type_name,
                        edge_kind="use_type",
                    )
                )

        # Extract references (identifiers that aren't calls, types, or templates)
        self._extract_references(
            body_node, src_qualified_name, edges, source,
            call_targets, seen_types, seen_templates
        )

    def _extract_references(
        self,
        body_node: Node,
        src_qualified_name: str,
        edges: list[EdgeInfo],
        source: str,
        call_targets: set[str],
        type_names: set[str],
        template_names: set[str],
    ) -> None:
        """Extract reference edges for identifiers that aren't calls or types.

        This captures usages like accessing global variables, constants, or enum values.
        """
        seen_refs: set[str] = set()

        # Find identifier usages
        identifiers = self._find_nodes_by_type(body_node, "identifier")
        for ident in identifiers:
            name = self._get_node_text(ident, source)
            if not name or name in seen_refs:
                continue
            # Skip if already recorded as call, type, or template
            if name in call_targets or name in type_names or name in template_names:
                continue
            # Skip C++ keywords and builtins
            if self._is_cpp_keyword(name) or self._is_builtin(name):
                continue
            # Skip if lowercase (likely local variable)
            # Focus on UPPER_CASE (constants), kCamelCase, or CamelCase
            if name[0].islower() and not name.startswith("k"):
                continue
            # Skip common loop variables
            if name in ("i", "j", "k", "n", "it", "iter"):
                continue

            seen_refs.add(name)
            edges.append(
                EdgeInfo(
                    src_qualified_name=src_qualified_name,
                    dst_qualified_name=name,
                    edge_kind="reference",
                )
            )

        # Find qualified identifier usages (namespace::constant, Enum::Value)
        qualified_ids = self._find_nodes_by_type(body_node, "qualified_identifier")
        for qid in qualified_ids:
            qname = self._get_node_text(qid, source)
            if not qname or qname in seen_refs:
                continue
            if qname in call_targets or qname in type_names or qname in template_names:
                continue
            # Check if last part is UPPER_CASE or enum-like
            parts = qname.split("::")
            if parts and (parts[-1].isupper() or parts[-1][0].isupper()):
                seen_refs.add(qname)
                edges.append(
                    EdgeInfo(
                        src_qualified_name=src_qualified_name,
                        dst_qualified_name=qname,
                        edge_kind="reference",
                    )
                )

    def _is_cpp_keyword(self, name: str) -> bool:
        """Check if name is a C++ keyword."""
        keywords = {
            "if", "else", "for", "while", "do", "switch", "case", "default",
            "break", "continue", "return", "goto", "try", "catch", "throw",
            "class", "struct", "union", "enum", "namespace", "template",
            "public", "private", "protected", "virtual", "override", "final",
            "const", "static", "extern", "mutable", "volatile", "inline",
            "constexpr", "consteval", "constinit", "explicit", "friend",
            "typedef", "using", "typename", "auto", "decltype", "nullptr",
            "true", "false", "this", "new", "delete", "sizeof", "alignof",
        }
        return name in keywords

    def _parse_function_declarator(
        self, node: Node, source: str
    ) -> tuple[Optional[str], list[str]]:
        """Parse function declarator to extract name and parameter types."""
        name: Optional[str] = None
        param_types: list[str] = []

        if node.type == "function_declarator":
            declarator = node.child_by_field_name("declarator")
            if declarator:
                name = self._get_node_text(declarator, source)

            params = node.child_by_field_name("parameters")
            if params:
                param_types = self._extract_parameter_types(params, source)

        elif node.type == "pointer_declarator":
            for child in node.children:
                if child.type == "function_declarator":
                    return self._parse_function_declarator(child, source)

        elif node.type == "reference_declarator":
            for child in node.children:
                if child.type == "function_declarator":
                    return self._parse_function_declarator(child, source)

        return name, param_types

    def _extract_parameter_types(self, params_node: Node, source: str) -> list[str]:
        """Extract parameter types from parameter list."""
        types: list[str] = []

        for child in params_node.children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node:
                    type_text = self._get_node_text(type_node, source)
                    types.append(type_text)
            elif child.type == "variadic_parameter_declaration":
                types.append("...")

        return types

    def _get_access_specifier(self, node: Node, source: str) -> str:
        """Extract access specifier (public/private/protected)."""
        text = self._get_node_text(node, source).rstrip(":")
        if text in ("public", "private", "protected"):
            return text
        return "private"

    def _find_nodes_by_type(self, node: Node, node_type: str) -> list[Node]:
        """Recursively find all nodes of a given type."""
        results: list[Node] = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes_by_type(child, node_type))
        return results

    def _find_type_identifiers(self, node: Node) -> list[Node]:
        """Find type identifier nodes."""
        results: list[Node] = []
        if node.type in ("type_identifier", "qualified_identifier"):
            results.append(node)
        for child in node.children:
            results.extend(self._find_type_identifiers(child))
        return results

    def _get_node_text(self, node: Node, source: str) -> str:
        """Get text content of a node."""
        return source[node.start_byte:node.end_byte]

    def _is_builtin(self, name: str) -> bool:
        """Check if a function name is a builtin/operator."""
        builtins = {
            "sizeof", "alignof", "typeid", "new", "delete",
            "static_cast", "dynamic_cast", "const_cast", "reinterpret_cast",
        }
        return name in builtins or name.startswith("operator")

    def _is_builtin_type(self, type_name: str) -> bool:
        """Check if a type is a builtin type."""
        builtins = {
            "int", "char", "bool", "float", "double", "void",
            "short", "long", "unsigned", "signed",
            "int8_t", "int16_t", "int32_t", "int64_t",
            "uint8_t", "uint16_t", "uint32_t", "uint64_t",
            "size_t", "ptrdiff_t", "nullptr_t",
            "auto", "decltype",
        }
        base_type = type_name.split("<")[0].split("::")[-1].strip()
        return base_type in builtins

    def _get_template_name(self, template_type: Node, source: str) -> Optional[str]:
        """Extract template name from template_type node (e.g., 'vector' from 'vector<int>')."""
        for child in template_type.children:
            if child.type in ("type_identifier", "qualified_identifier"):
                return self._get_node_text(child, source)
        return None

    def _is_std_template(self, name: str) -> bool:
        """Check if a template name is from the standard library."""
        std_templates = {
            "vector", "map", "set", "unordered_map", "unordered_set",
            "list", "deque", "array", "pair", "tuple",
            "shared_ptr", "unique_ptr", "weak_ptr",
            "optional", "variant", "any",
            "function", "bind", "ref", "cref",
            "string", "wstring", "basic_string",
            "stringstream", "istringstream", "ostringstream",
            "fstream", "ifstream", "ofstream",
            "thread", "mutex", "lock_guard", "unique_lock",
            "future", "promise", "async",
            "initializer_list", "allocator",
        }
        # Get base name (remove std:: prefix if present)
        base_name = name.split("::")[-1] if "::" in name else name
        return base_name in std_templates or name.startswith("std::")
