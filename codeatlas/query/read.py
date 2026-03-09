"""Source code reading query for CodeAtlas."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..storage.db import Database


@dataclass
class ReadResult:
    """Result of reading symbol source code."""

    symbol_id: int
    name: str
    qualified_name: str
    symbol_kind: str
    file_path: str
    start_line: int
    end_line: int
    source: str


def read_symbol(
    db: Database,
    workspace: Path,
    symbol_id: int,
    part: str = "definition",
) -> Optional[ReadResult]:
    """Read source code for a symbol.

    Args:
        db: Database connection
        workspace: Workspace root path
        symbol_id: Symbol ID to read
        part: "declaration" or "definition"

    Returns:
        ReadResult with source code, or None if not found
    """
    symbol = db.get_symbol_by_id(symbol_id)
    if not symbol:
        return None

    if part == "declaration":
        file_id = symbol.decl_file_id
        start_line = symbol.decl_start
        end_line = symbol.decl_end
    else:
        file_id = symbol.def_file_id
        start_line = symbol.def_start
        end_line = symbol.def_end

    file_record = db.get_file_by_id(file_id)
    if not file_record:
        return None

    file_path = Path(file_record.path)
    if not file_path.is_absolute():
        file_path = workspace / file_path

    if not file_path.exists():
        return None

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        source_lines = lines[start_line - 1 : end_line]
        source = "\n".join(source_lines)
    except Exception:
        return None

    return ReadResult(
        symbol_id=symbol_id,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        symbol_kind=symbol.symbol_kind,
        file_path=file_record.path,
        start_line=start_line,
        end_line=end_line,
        source=source,
    )


def read_symbol_by_qualified_name(
    db: Database,
    workspace: Path,
    qualified_name: str,
    part: str = "definition",
) -> Optional[ReadResult]:
    """Read source code for a symbol by qualified name.

    Args:
        db: Database connection
        workspace: Workspace root path
        qualified_name: Fully qualified symbol name
        part: "declaration" or "definition"

    Returns:
        ReadResult with source code, or None if not found
    """
    symbols = db.search_symbols(qualified_name, exact=True)
    if not symbols:
        symbols = [
            s for s in db.get_all_symbols() if s.qualified_name == qualified_name
        ]

    if not symbols:
        return None

    symbol = symbols[0]
    if symbol.id is None:
        return None

    return read_symbol(db, workspace, symbol.id, part)
