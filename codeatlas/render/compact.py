"""Compact text renderer for CodeAtlas."""

from os.path import commonprefix
from pathlib import Path
from typing import Optional

from ..query.inspect import InspectResult, SymbolConnections, SymbolInfo
from ..query.overview import OverviewResult
from ..query.read import ReadResult
from ..query.search import SearchResult
from ..query.trace import TraceResult


def render_overview(result: OverviewResult) -> str:
    """Render Level 0 overview as compact text.

    File view:
        [src/auth/] -> [src/db/], [src/crypto/]
        [src/api/]  -> [src/auth/], [src/models/]
        [test/]  (standalone)

    Logic view:
        auth:: -> db::, crypto::
        api::  -> auth::, models::
        utils::  (standalone)
    """
    lines: list[str] = []

    if result.view_type == "file":
        for dep in result.dependencies:
            if dep.targets:
                targets = ", ".join(f"[{t}/]" for t in dep.targets)
                lines.append(f"[{dep.source}/] -> {targets}")
            else:
                lines.append(f"[{dep.source}/]  (standalone)")
    else:
        for dep in result.dependencies:
            if dep.targets:
                targets = ", ".join(f"{t}::" for t in dep.targets)
                lines.append(f"{dep.source}:: -> {targets}")
            else:
                lines.append(f"{dep.source}::  (standalone)")

    return "\n".join(lines)


def render_search(results: list[SearchResult]) -> str:
    """Render search results as compact text.

    Format:
        search("authenticate")

        #42 [src/auth/handler.py:42-67] authenticate(...)  [low]
        #58 [src/auth/session.py:28] SessionManager.authenticate  [low]
    """
    if not results:
        return "No results found."

    lines: list[str] = []

    paths = [r.file_path for r in results]
    common = _find_common_prefix(paths)

    for r in results:
        rel_path = _relative_path(r.file_path, common)
        trust_tag = f"  [{r.trust_level}]"
        lines.append(
            f"#{r.symbol_id} [{rel_path}:{r.start_line}-{r.end_line}] "
            f"{r.qualified_name}{trust_tag}"
        )

    return "\n".join(lines)


def render_inspect(result: InspectResult) -> str:
    """Render inspect result as compact text.

    Level 1 format:
        @ src/auth/

        [handler.py:42-67] authenticate(credentials: Credentials) -> Result
        [session.py:10-35] SessionManager
          members: login(user: User) -> Session
                   logout(session: Session) -> None

    Level 2 format:
        @ src/auth/

        [handler.py] authenticate
          -> [../db/query.py] UserDAO
          -> [../crypto/hash.py] verify_password
    """
    lines: list[str] = []
    lines.append(f"@ {result.target}")
    lines.append("")

    paths = [s.file_path for s in result.symbols]
    common = _find_common_prefix(paths)

    if result.level == 1:
        for symbol in result.symbols:
            lines.append(_render_symbol_level1(symbol, common))
    else:
        for conn in result.connections:
            lines.extend(_render_connections(conn, common))

        symbols_without_conn = [
            s for s in result.symbols
            if not any(c.symbol.symbol_id == s.symbol_id for c in result.connections)
        ]
        for symbol in symbols_without_conn:
            lines.append(_render_symbol_level1(symbol, common))

    return "\n".join(lines)


def render_read(result: ReadResult) -> str:
    """Render read result as source code with context.

    Format:
        [src/auth/handler.py:42-67] authenticate

        def authenticate(credentials: Credentials) -> Result:
            ...
    """
    lines: list[str] = []
    lines.append(f"[{result.file_path}:{result.start_line}-{result.end_line}] {result.name}")
    lines.append("")
    lines.append(result.source)
    return "\n".join(lines)


def _render_symbol_level1(symbol: SymbolInfo, common_prefix: str) -> str:
    """Render a symbol for Level 1 inspection."""
    rel_path = _relative_path(symbol.file_path, common_prefix)
    header = f"[{rel_path}:{symbol.start_line}-{symbol.end_line}] {symbol.name}"

    if symbol.symbol_kind == "class" and symbol.members:
        member_lines = []
        for i, member in enumerate(symbol.members):
            prefix = "  members: " if i == 0 else "           "
            member_lines.append(f"{prefix}{member.name}")
        return header + "\n" + "\n".join(member_lines)

    return header


def _render_connections(conn: SymbolConnections, common_prefix: str) -> list[str]:
    """Render connections for Level 2 inspection."""
    lines: list[str] = []
    rel_path = _relative_path(conn.symbol.file_path, common_prefix)

    lines.append(f"[{rel_path}] {conn.symbol.name}")
    for c in conn.connections:
        dst_rel_path = _relative_path(c.dst_file_path, common_prefix)
        dst_name = c.dst_qualified_name.split(".")[-1]
        lines.append(f"  -> [{dst_rel_path}] {dst_name}")

    return lines


def _find_common_prefix(paths: list[str]) -> str:
    """Find common directory prefix among paths."""
    if not paths:
        return ""

    dirs = [str(Path(p).parent) for p in paths]
    common = commonprefix(dirs)

    if common and not common.endswith("/"):
        common = str(Path(common).parent)

    return common


def _relative_path(path: str, common_prefix: str) -> str:
    """Get path relative to common prefix."""
    if not common_prefix:
        return path

    try:
        return str(Path(path).relative_to(common_prefix))
    except ValueError:
        return path


def render_trace(result: TraceResult) -> str:
    """Render trace result as compact text.

    Format:
        trace #42 (both)

        center: [src/auth/handler.py:42-67] authenticate

        -> [src/db/query.py:10-35] UserDAO (call)
        -> [src/crypto/hash.py:5-20] verify_password (call)

        <- [src/api/routes.py:100-120] login_handler (call)
    """
    lines: list[str] = []
    lines.append(f"trace #{result.center.symbol_id} ({result.direction})")
    lines.append("")
    lines.append(
        f"center: [{result.center.file_path}:{result.center.start_line}-{result.center.end_line}] "
        f"{result.center.name}"
    )
    lines.append("")

    node_map = {n.symbol_id: n for n in result.nodes}
    node_map[result.center.symbol_id] = result.center

    forward_edges = [e for e in result.edges if e.src_id == result.center.symbol_id]
    backward_edges = [e for e in result.edges if e.dst_id == result.center.symbol_id]

    for edge in forward_edges:
        dst_node = node_map.get(edge.dst_id)
        if dst_node:
            lines.append(
                f"-> [{dst_node.file_path}:{dst_node.start_line}-{dst_node.end_line}] "
                f"{dst_node.name} ({edge.edge_kind})"
            )

    if forward_edges and backward_edges:
        lines.append("")

    for edge in backward_edges:
        src_node = node_map.get(edge.src_id)
        if src_node:
            lines.append(
                f"<- [{src_node.file_path}:{src_node.start_line}-{src_node.end_line}] "
                f"{src_node.name} ({edge.edge_kind})"
            )

    if not forward_edges and not backward_edges:
        lines.append("No connections found.")

    return "\n".join(lines)
