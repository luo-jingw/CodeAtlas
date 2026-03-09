"""CLI entry point for CodeAtlas."""

from pathlib import Path
from typing import Optional

import typer

from .config import Config
from .indexer import Indexer
from .query.inspect import inspect as query_inspect
from .query.overview import overview as query_overview
from .query.read import read_symbol, read_symbol_by_qualified_name
from .query.search import search_symbols
from .query.trace import trace as query_trace
from .render.compact import render_inspect, render_overview, render_read, render_search, render_trace
from .storage.db import Database
from .trust.manager import TrustManager

DESIGN_GUIDE = """
Design for CodeAtlas Compatibility:

  1. Explicit Dependencies
     - All dependencies via explicit import/include
     - No wildcard imports, no dynamic imports

  2. One File, One Purpose
     - Each file has a single responsibility
     - File name reflects its purpose

  3. Interface Separation
     - C++: .h/.cpp separation
     - Python: __init__.py for exports only

  4. Complete Type Annotations
     - All function signatures with full type hints
     - Use dataclass/TypedDict, not bare dict

  5. Avoid Hidden Patterns
     - No metaclass dynamic generation
     - No __getattr__ proxying
     - No signature-modifying decorators

  Summary: Make everything explicit.
"""

app = typer.Typer(
    name="codeatlas",
    help="LLM-oriented code atlas for understanding code structure.",
    epilog=DESIGN_GUIDE,
    no_args_is_help=True,
)

DB_NAME = ".codeatlas.db"


def get_db_path(workspace: Path) -> Path:
    return workspace / DB_NAME


@app.command()
def init(
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory to index",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Initialize CodeAtlas index for the workspace."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    if not workspace_path.exists():
        typer.echo(f"Error: Workspace {workspace_path} does not exist")
        raise typer.Exit(1)

    cfg = Config.load(workspace_path)
    db_path = get_db_path(workspace_path)

    db = Database(db_path)
    db.connect()
    db.init_schema()

    indexer = Indexer(cfg, db)
    typer.echo(f"Indexing {workspace_path}...")

    files_indexed, symbols_indexed, errors = indexer.index_workspace()

    db.close()

    typer.echo(f"Indexed {files_indexed} files, {symbols_indexed} symbols")
    if errors:
        typer.echo(f"Warnings: {len(errors)}")
        for error in errors[:5]:
            typer.echo(f"  - {error}")
        if len(errors) > 5:
            typer.echo(f"  ... and {len(errors) - 5} more")


@app.command()
def rebuild(
    path: Optional[Path] = typer.Argument(
        None,
        help="Specific file or directory to rebuild",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Rebuild index for specified path or entire workspace.

    If path is provided, only that file or directory will be re-indexed.
    Trust levels are inherited via signature_hash matching.
    """
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    cfg = Config.load(workspace_path)
    db = Database(db_path)
    db.connect()

    indexer = Indexer(cfg, db)

    # For full workspace rebuild, save trust cache first
    if path is None:
        indexer._save_trust_cache()
        typer.echo(f"Rebuilding {workspace_path}...")
    else:
        typer.echo(f"Rebuilding {path}...")

    files_indexed, symbols_indexed, errors = indexer.index_workspace(path)

    db.close()

    typer.echo(f"Rebuilt {files_indexed} files, {symbols_indexed} symbols")


@app.command()
def overview(
    filter_path: Optional[str] = typer.Option(
        None,
        "--filter",
        "-f",
        help="Filter to subdirectory or namespace",
    ),
    view: str = typer.Option(
        "file",
        "--view",
        "-v",
        help="View type: file or logic",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Show Level 0 module dependency graph."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    result = query_overview(db, workspace_path, filter_path, view)
    output = render_overview(result)

    db.close()
    typer.echo(output)


@app.command()
def inspect(
    target: str = typer.Argument(..., help="Target path, namespace, or #id"),
    level: int = typer.Option(
        1,
        "--level",
        "-l",
        help="Expansion level (1 or 2)",
    ),
    max_items: int = typer.Option(
        50,
        "--max-items",
        "-m",
        help="Maximum symbols to return",
    ),
    force_expand: bool = typer.Option(
        False,
        "--force-expand",
        help="Override high trust folding",
    ),
    detail: str = typer.Option(
        "class",
        "--detail",
        "-d",
        help="Detail level: class or method",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Inspect a module, namespace, or class."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    result = query_inspect(db, workspace_path, target, level, max_items, force_expand, detail)

    db.close()

    if not result:
        typer.echo(f"No results found for '{target}'")
        raise typer.Exit(1)

    output = render_inspect(result)
    typer.echo(output)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    exact: bool = typer.Option(
        False,
        "--exact",
        "-e",
        help="Exact match instead of substring",
    ),
    kind: Optional[str] = typer.Option(
        None,
        "--kind",
        "-k",
        help="Filter by symbol kind",
    ),
    path: Optional[str] = typer.Option(
        None,
        "--path",
        "-p",
        help="Filter by file path",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Search for symbols matching the query."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    results = search_symbols(db, query, exact=exact, kind=kind, path=path)

    db.close()

    output = render_search(results)
    typer.echo(output)


@app.command()
def read(
    symbol: str = typer.Argument(..., help="Symbol #id or qualified name"),
    part: str = typer.Option(
        "definition",
        "--part",
        "-p",
        help="Part to read: declaration or definition",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Read source code for a symbol."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    if symbol.startswith("#"):
        try:
            symbol_id = int(symbol[1:])
            result = read_symbol(db, workspace_path, symbol_id, part)
        except ValueError:
            typer.echo(f"Invalid symbol ID: {symbol}")
            db.close()
            raise typer.Exit(1)
    else:
        result = read_symbol_by_qualified_name(db, workspace_path, symbol, part)

    db.close()

    if not result:
        typer.echo(f"Symbol not found: {symbol}")
        raise typer.Exit(1)

    output = render_read(result)
    typer.echo(output)


@app.command()
def trace(
    target: str = typer.Argument(..., help="Symbol #id or qualified name"),
    direction: str = typer.Option(
        "both",
        "--direction",
        "-d",
        help="Direction: forward, backward, or both",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Trace connections for a symbol."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    result = query_trace(db, workspace_path, target, direction)

    db.close()

    if not result:
        typer.echo(f"Symbol not found: {target}")
        raise typer.Exit(1)

    output = render_trace(result)
    typer.echo(output)


@app.command()
def trust(
    target: str = typer.Argument(..., help="Symbol #id, qualified name, or path pattern"),
    level: str = typer.Argument(..., help="Trust level: high or low"),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace directory",
    ),
) -> None:
    """Set trust level for a symbol or path."""
    workspace_path = workspace or Path.cwd()
    workspace_path = workspace_path.resolve()

    db_path = get_db_path(workspace_path)
    if not db_path.exists():
        typer.echo("Error: Index not found. Run 'codeatlas init' first.")
        raise typer.Exit(1)

    if level not in ("high", "low"):
        typer.echo("Error: Trust level must be 'high' or 'low'")
        raise typer.Exit(1)

    db = Database(db_path)
    db.connect()

    manager = TrustManager(db)
    success = manager.set_trust(target, level)

    db.close()

    if success:
        typer.echo(f"Set trust level for '{target}' to {level}")
    else:
        typer.echo(f"No matching symbols found for '{target}'")
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
