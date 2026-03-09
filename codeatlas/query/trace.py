"""Trace query for CodeAtlas - connection relationship tracing."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..storage.db import Database, SymbolRecord


@dataclass
class TraceNode:
    """Node in trace result."""

    symbol_id: int
    name: str
    qualified_name: str
    symbol_kind: str
    file_path: str
    start_line: int
    end_line: int
    trust_level: str


@dataclass
class TraceEdge:
    """Edge in trace result."""

    src_id: int
    dst_id: int
    edge_kind: str


@dataclass
class TraceResult:
    """Result of trace query."""

    center: TraceNode
    nodes: list[TraceNode]
    edges: list[TraceEdge]
    direction: str


def trace(
    db: Database,
    workspace: Path,
    target: str,
    direction: str = "both",
) -> Optional[TraceResult]:
    """Trace connections for a symbol.

    Args:
        db: Database connection
        workspace: Workspace root path
        target: Symbol #id or qualified name
        direction: "forward", "backward", or "both"

    Returns:
        TraceResult with connected nodes and edges
    """
    if target.startswith("#"):
        try:
            symbol_id = int(target[1:])
        except ValueError:
            return None
        symbol = db.get_symbol_by_id(symbol_id)
    else:
        symbols = [
            s for s in db.get_all_symbols()
            if s.qualified_name == target or s.name == target
        ]
        symbol = symbols[0] if symbols else None

    if not symbol or symbol.id is None:
        return None

    files = {f.id: f for f in db.get_all_files()}
    all_symbols = {s.id: s for s in db.get_all_symbols()}

    file_record = files.get(symbol.file_id)
    if not file_record:
        return None

    center = _to_trace_node(symbol, file_record.path)
    nodes: list[TraceNode] = []
    edges: list[TraceEdge] = []
    seen_ids: set[int] = {symbol.id}

    if direction in ("forward", "both"):
        forward_edges = db.get_edges_from_symbol(symbol.id)
        for edge in forward_edges:
            dst_symbol = all_symbols.get(edge.dst_symbol_id)
            if dst_symbol and dst_symbol.id not in seen_ids:
                if dst_symbol.trust_level == "high":
                    continue

                dst_file = files.get(dst_symbol.file_id)
                if dst_file:
                    nodes.append(_to_trace_node(dst_symbol, dst_file.path))
                    seen_ids.add(dst_symbol.id)  # type: ignore

            edges.append(
                TraceEdge(
                    src_id=symbol.id,
                    dst_id=edge.dst_symbol_id,
                    edge_kind=edge.edge_kind,
                )
            )

    if direction in ("backward", "both"):
        backward_edges = db.get_edges_to_symbol(symbol.id)
        for edge in backward_edges:
            src_symbol = all_symbols.get(edge.src_symbol_id)
            if src_symbol and src_symbol.id not in seen_ids:
                if src_symbol.trust_level == "high":
                    continue

                src_file = files.get(src_symbol.file_id)
                if src_file:
                    nodes.append(_to_trace_node(src_symbol, src_file.path))
                    seen_ids.add(src_symbol.id)  # type: ignore

            edges.append(
                TraceEdge(
                    src_id=edge.src_symbol_id,
                    dst_id=symbol.id,
                    edge_kind=edge.edge_kind,
                )
            )

    return TraceResult(
        center=center,
        nodes=nodes,
        edges=edges,
        direction=direction,
    )


def _to_trace_node(symbol: SymbolRecord, file_path: str) -> TraceNode:
    """Convert SymbolRecord to TraceNode."""
    return TraceNode(
        symbol_id=symbol.id,  # type: ignore
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        symbol_kind=symbol.symbol_kind,
        file_path=file_path,
        start_line=symbol.def_start,
        end_line=symbol.def_end,
        trust_level=symbol.trust_level,
    )
