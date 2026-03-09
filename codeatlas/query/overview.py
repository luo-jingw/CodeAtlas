"""Overview query for CodeAtlas - Level 0 module dependency graph."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..storage.db import Database


@dataclass
class ModuleDependency:
    """Dependency between modules/directories."""

    source: str
    targets: list[str]


@dataclass
class OverviewResult:
    """Result of overview query."""

    view_type: str
    dependencies: list[ModuleDependency]


def overview(
    db: Database,
    workspace: Path,
    filter_path: Optional[str] = None,
    view: str = "file",
) -> OverviewResult:
    """Generate Level 0 module dependency overview.

    Args:
        db: Database connection
        workspace: Workspace root path
        filter_path: Optional path filter for subdirectory/namespace
        view: "file" for file_edges based view, "logic" for symbol edges aggregation

    Returns:
        OverviewResult with module dependencies
    """
    if view == "file":
        return _file_view_overview(db, workspace, filter_path)
    else:
        return _logic_view_overview(db, workspace, filter_path)


def _file_view_overview(
    db: Database,
    workspace: Path,
    filter_path: Optional[str],
) -> OverviewResult:
    """Generate file-based overview from file_edges."""
    file_edges = db.get_all_file_edges()
    files = {f.id: f for f in db.get_all_files()}

    # Collect all modules from all files
    all_modules: set[str] = set()
    for f in files.values():
        module_dir = _get_module_dir(f.path, workspace)
        if module_dir and module_dir != ".":
            # Get top-level directory
            top_level = module_dir.split("/")[0]
            if filter_path is None or top_level.startswith(filter_path):
                all_modules.add(top_level)

    dir_deps: dict[str, set[str]] = defaultdict(set)

    for edge in file_edges:
        src_file = files.get(edge.src_file_id)
        dst_file = files.get(edge.dst_file_id)

        if not src_file or not dst_file:
            continue

        src_dir = _get_module_dir(src_file.path, workspace)
        dst_dir = _get_module_dir(dst_file.path, workspace)

        if filter_path and not src_dir.startswith(filter_path):
            continue

        if src_dir != dst_dir:
            dir_deps[src_dir].add(dst_dir)

    # Include all modules, even those without external dependencies
    for module in all_modules:
        if module not in dir_deps:
            dir_deps[module] = set()

    dependencies = [
        ModuleDependency(source=src, targets=sorted(targets))
        for src, targets in sorted(dir_deps.items())
    ]

    return OverviewResult(view_type="file", dependencies=dependencies)


def _logic_view_overview(
    db: Database,
    workspace: Path,
    filter_path: Optional[str],
) -> OverviewResult:
    """Generate logic-based overview from symbol edges aggregation."""
    edges = db.get_all_edges()
    symbols = {s.id: s for s in db.get_all_symbols()}

    # Collect all modules from all symbols
    all_modules: set[str] = set()
    for s in symbols.values():
        module_name = _get_module_name(s.qualified_name)
        if module_name:
            if filter_path is None or module_name.startswith(filter_path):
                all_modules.add(module_name)

    module_deps: dict[str, set[str]] = defaultdict(set)

    for edge in edges:
        src_symbol = symbols.get(edge.src_symbol_id)
        dst_symbol = symbols.get(edge.dst_symbol_id)

        if not src_symbol or not dst_symbol:
            continue

        src_module = _get_module_name(src_symbol.qualified_name)
        dst_module = _get_module_name(dst_symbol.qualified_name)

        if filter_path and not src_module.startswith(filter_path):
            continue

        if src_module != dst_module:
            module_deps[src_module].add(dst_module)

    # Include all modules, even those without external dependencies
    for module in all_modules:
        if module not in module_deps:
            module_deps[module] = set()

    dependencies = [
        ModuleDependency(source=src, targets=sorted(targets))
        for src, targets in sorted(module_deps.items())
    ]

    return OverviewResult(view_type="logic", dependencies=dependencies)


def _get_module_dir(file_path: str, workspace: Path) -> str:
    """Extract module directory from file path."""
    path = Path(file_path)
    try:
        rel_path = path.relative_to(workspace)
        return str(rel_path.parent)
    except ValueError:
        return str(path.parent)


def _get_module_name(qualified_name: str) -> str:
    """Extract module name from qualified name (top-level namespace/module).

    Handles both Python (.) and C++ (::) separators.
    """
    # Check if it's C++ style (::) or Python style (.)
    if "::" in qualified_name:
        parts = qualified_name.split("::")
        # Return top-level namespace for C++
        return parts[0] if parts[0] else qualified_name
    else:
        parts = qualified_name.split(".")
        if len(parts) >= 3:
            return ".".join(parts[:2])
        elif len(parts) == 2:
            return parts[0]
        return qualified_name
