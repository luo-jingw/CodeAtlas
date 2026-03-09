# CodeAtlas

LLM-oriented code atlas for understanding code structure.

## Overview

CodeAtlas is a tool that helps LLMs understand code structure with minimal tokens. It extracts symbols, relationships, and module dependencies from source code and provides multi-level views for efficient code navigation.

**Core principle**: Auxiliary tool, not infrastructure. Does not participate in build, version control, or testing. Index can be rebuilt from source at any time.

## Installation

```bash
# From source
pip install -e .

# Or use compiled binary
./dist/codeatlas
```

### Dependencies

- Python >= 3.10
- tree-sitter, tree-sitter-python, tree-sitter-cpp
- typer, xxhash, pyyaml

## Quick Start

```bash
# Initialize index for current directory
codeatlas init

# View module dependencies
codeatlas overview

# Inspect a module
codeatlas inspect src/auth/

# Search for symbols
codeatlas search authenticate

# Read symbol source code
codeatlas read "#42"
```

## Commands

### `init`

Initialize or rebuild the code index.

```bash
codeatlas init [--workspace PATH] [--config PATH]
```

### `overview`

Display Level 0 module dependency graph.

```bash
codeatlas overview [--filter PATH] [--view file|logic]
```

Output example (file view):
```
[src/auth/] -> [src/db/], [src/crypto/]
[src/api/]  -> [src/auth/], [src/models/]
[tests/]  (standalone)
```

### `inspect`

Display module/class contents at specified detail level.

```bash
codeatlas inspect <target> [--level 1|2] [--detail method]
```

- Level 1: Symbol signatures only
- Level 2: Symbol connections (class-level by default, `--detail method` for method-level)

### `search`

Search symbols by name.

```bash
codeatlas search <query> [--exact] [--kind function|class|method] [--path PATH]
```

Output example:
```
#42 [src/auth/handler.py:42-67] authenticate(credentials: Credentials) -> Result  [low]
#58 [src/auth/session.py:28] SessionManager.authenticate  [low]
```

### `read`

Read symbol source code.

```bash
codeatlas read <symbol> [--part declaration|definition]
```

Symbol can be `#id` (from search results) or qualified name.

### `trace`

Show symbol connections (depth=1).

```bash
codeatlas trace <target> [--direction forward|backward|both]
```

### `trust`

Set trust level for path or symbol.

```bash
codeatlas trust <target> high|low
```

- `high`: Only show signatures, auto-fold in queries
- `low`: Show full details (default for workspace code)

### `rebuild`

Rebuild index, preserving trust levels via signature_hash matching.

```bash
codeatlas rebuild [path]
```

## Configuration

Optional `.codeatlas.yaml`:

```yaml
workspace: ./
language: auto
trust:
  high:
    - numpy
    - torch
    - src/utils/stable_module.py
  low:
    - src/experimental/*
exclude:
  - "*.generated.py"
  - build/
  - __pycache__/
```

## Supported Languages

| Language | Extensions | Status |
|----------|------------|--------|
| Python | .py, .pyi | Full support |
| C/C++ | .c, .cc, .cpp, .h, .hpp | Full support |

## Design for CodeAtlas Compatibility

To maximize CodeAtlas effectiveness, follow these system design principles:

### 1. Explicit Dependencies

- All dependencies via explicit `import`/`include`
- No wildcard imports (`from x import *`)
- No dynamic imports or implicit module communication
- C++: Each file includes its direct dependencies

### 2. One File, One Purpose

- Each file has a single responsibility
- File name reflects its purpose
- No unrelated classes or functions in one file

### 3. Interface Separation

- C++: `.h`/`.cpp` separation
- Python: `__init__.py` for exports only, use `Protocol`/`ABC` for interfaces

### 4. Complete Type Annotations

- All function signatures with full type hints
- Use `dataclass` or `TypedDict`, not bare `dict`

### 5. Avoid Hidden Patterns

Avoid these patterns that CodeAtlas cannot track:
- Metaclass dynamic generation
- `__getattr__` proxying
- Decorators that modify signatures
- Complex macro-generated code

If unavoidable, provide explicit interface declarations nearby.

### Summary

**Four words: Make everything explicit.**

## Architecture

```
codeatlas/
  cli.py              # Entry point, typer commands
  config.py           # Configuration loading
  indexer.py          # Parse coordination
  parser/
    base.py           # Parser interface
    python.py         # Python language mapping
    cpp.py            # C/C++ language mapping
  storage/
    db.py             # SQLite operations
  query/
    overview.py       # Level 0 dependency graph
    inspect.py        # Level 1/2 symbol details
    search.py         # Symbol search
    read.py           # Source code reading
    trace.py          # Connection tracing
  trust/
    manager.py        # Trust level management
  render/
    compact.py        # Text rendering
```

## Database

SQLite single-file database (`.codeatlas.db`).

Key tables:
- `files`: Source files with content hash
- `symbols`: Extracted symbols with signature_hash, symbol_status (declaration_only/definition_only for C++)
- `edges`: Symbol-level connections (call, use_type, inherit, instantiate, reference)
- `file_edges`: File-level connections (import, include)
- `namespaces`: C++ namespace modeling
- `namespace_members`: Namespace-symbol associations
- `trust_log`: Trust level change history

## Limitations

By design, CodeAtlas does NOT:
- Perform precise call graph analysis
- Do type inference or dynamic dispatch resolution
- Expand C++ templates or macros
- Provide LSP-level semantic analysis

Goal: Usable but conservative index, no guarantee of complete semantic consistency.

## Build

```bash
# Compile to standalone binary
python -m nuitka --standalone --onefile --static-libpython=no \
  --include-package=codeatlas --output-dir=dist \
  codeatlas/__main__.py -o codeatlas
```

## License

MIT
