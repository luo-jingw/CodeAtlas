"""Symbol search query for CodeAtlas."""

import re
from dataclasses import dataclass
from typing import Optional

from ..storage.db import Database, SymbolRecord


@dataclass
class SearchResult:
    """Result of a symbol search."""

    symbol_id: int
    name: str
    qualified_name: str
    symbol_kind: str
    file_path: str
    start_line: int
    end_line: int
    trust_level: str


def search_symbols(
    db: Database,
    query: str,
    exact: bool = False,
    regex: bool = False,
    kind: Optional[str] = None,
    path: Optional[str] = None,
) -> list[SearchResult]:
    """Search for symbols matching the query.

    Args:
        db: Database connection
        query: Search query string
        exact: If True, match exact name; otherwise substring match
        regex: If True, use regex pattern matching
        kind: Filter by symbol_kind (function, class, method, etc.)
        path: Filter by file path pattern

    Returns:
        List of matching symbols, low trust first
    """
    if regex:
        symbols = db.search_symbols_all(kind=kind, path=path)
        pattern = re.compile(query)
        symbols = [s for s in symbols if pattern.search(s.name)]
    else:
        symbols = db.search_symbols(query, exact=exact, kind=kind, path=path)

    results: list[SearchResult] = []
    for symbol in symbols:
        file_record = db.get_file_by_id(symbol.file_id)
        if not file_record:
            continue

        results.append(
            SearchResult(
                symbol_id=symbol.id,  # type: ignore
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                symbol_kind=symbol.symbol_kind,
                file_path=file_record.path,
                start_line=symbol.def_start,
                end_line=symbol.def_end,
                trust_level=symbol.trust_level,
            )
        )

    results.sort(key=lambda r: (0 if r.trust_level == "low" else 1, r.symbol_id))
    return results
