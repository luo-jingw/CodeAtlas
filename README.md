# CodeAtlas

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## Problem

LLMs working with unfamiliar codebases face a dilemma:
- **Too little context**: Guessing leads to hallucinated APIs and broken imports
- **Too much context**: Dumping entire files wastes tokens and dilutes focus

What LLMs actually need: a structured map of symbols, dependencies, and module boundaries — not raw source code.

## Solution

CodeAtlas builds a lightweight, queryable index of your codebase:

```
Source Code → Tree-sitter Parsing → Symbol Graph → SQLite Index
                                                        ↓
                                    LLM ← Compact Output ← CLI Queries
```

**Core principle**: Auxiliary tool, not infrastructure. Index can be rebuilt from source at any time. Does not participate in build, version control, or testing.

**Best used with grep**: CodeAtlas and grep are complementary tools. Use CodeAtlas for structural queries (dependencies, symbol relationships, module boundaries) and grep for text patterns (error messages, string literals, TODOs). Together they provide both semantic understanding and raw search capability.

## Installation

```bash
# From source
pip install -e .

# Or use compiled binary (Linux x86_64)
./dist/codeatlas
```

**Dependencies**: Python >= 3.10, tree-sitter, tree-sitter-python, tree-sitter-cpp, typer

## Quick Start

```bash
# 1. Initialize index
codeatlas init

# 2. View module dependencies (use logic view for flat C++ directories)
codeatlas overview
codeatlas overview --view logic

# 3. Search for symbols
codeatlas search Manager
# Output: #42 [src/manager.py:15-89] Manager  [low]

# 4. View symbol details and connections
codeatlas inspect "#42" --level 2
codeatlas trace "#42"

# 5. Read source code
codeatlas read "#42"
```

## Command Reference

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `init` | Initialize index | `--workspace PATH` |
| `overview` | Module dependency graph | `--view file\|logic`, `--filter PATH` |
| `inspect` | Symbol details | `--level 1\|2`, `--detail method` |
| `search` | Symbol search | `--exact`, `--kind`, `--path` |
| `read` | Read source code | `--part declaration\|definition` |
| `trace` | Connection tracing | `--direction forward\|backward\|both` |
| `trust` | Set trust level | `high\|low` |
| `rebuild` | Rebuild index | `[path]` supports single file/directory |

### Output Examples

**overview (file view)**
```
[src/auth/] -> [src/db/], [src/crypto/]
[src/api/]  -> [src/auth/], [src/models/]
[tests/]  (standalone)
```

**search**
```
#42 [src/auth/handler.py:42-67] authenticate(credentials: Credentials) -> Result  [low]
#58 [src/auth/session.py:28] SessionManager.authenticate  [low]
```

**trace**
```
Symbol: #42 authenticate
Forward (calls):
  -> #58 SessionManager.authenticate [call]
  -> #67 validate_credentials [call]
Backward (called by):
  <- #23 login_handler [call]
```

## Supported Languages

| Language | Extensions | Features |
|----------|------------|----------|
| Python | .py, .pyi | Full support |
| C/C++ | .c, .cc, .cpp, .h, .hpp | Declaration/definition merging, namespace support |

### Tips for C++ Projects

- **Flat directory structure**: Use `--view logic` to aggregate by namespace/class
- **Include tracking**: Only `#include "..."` creates file_edges, `#include <...>` is ignored
- **Declaration/definition merging**: .h declarations and .cpp definitions are automatically merged via signature_hash
- **Forward declarations**: `class Foo;` does not create symbols to avoid duplicates

## Configuration

Optional `.codeatlas.yaml`:

```yaml
workspace: ./
trust:
  high:
    - vendor/
    - src/stable/*
  low:
    - src/experimental/*
exclude:
  - build/
  - __pycache__/
  - "*.generated.py"
```

## Design Compatibility

To maximize CodeAtlas effectiveness, follow these principles:

1. **Explicit dependencies** - No wildcard imports, no dynamic imports
2. **Single responsibility** - One file does one thing
3. **Interface separation** - C++ .h/.cpp separation, Python uses Protocol/ABC
4. **Complete type annotations** - Full type hints on function signatures
5. **Avoid implicit patterns** - No metaclass dynamic generation, no `__getattr__` proxying

**Summary: Make everything explicit.** See `codeatlas --help` for details.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Source Files                             │
│                   (.py, .pyi, .cpp, .h, ...)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Tree-sitter Parsing                          │
│         Extract symbols, signatures, relationships             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Symbol Graph                               │
│   symbols: class, function, method, variable                   │
│   edges: call, inherit, use_type, reference                    │
│   file_edges: import, include                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SQLite Index (.codeatlas.db)                 │
│        Persistent storage with signature_hash tracking         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CLI Queries                               │
│          overview | inspect | search | trace | read            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Compact Output                             │
│            Token-efficient format for LLM consumption          │
└─────────────────────────────────────────────────────────────────┘
```

## Performance

- **Parallel parsing**: Files are parsed concurrently using ThreadPoolExecutor
- **Batch commits**: All database writes are batched in a single transaction
- **Query caching**: Symbol lookups are cached between indexing phases

Typical performance on a medium-sized project (~200 files):
- `init`: < 1s
- `rebuild [path]`: < 0.2s

## Database

SQLite single-file: `.codeatlas.db`

| Table | Content |
|-------|---------|
| `files` | Source files with content hash |
| `symbols` | Symbols with signature_hash, symbol_status |
| `edges` | Symbol connections (call, use_type, inherit, instantiate, reference) |
| `file_edges` | File connections (import, include) |
| `namespaces` | C++ namespace modeling |
| `trust_log` | Trust level change history |

## Limitations

By design, CodeAtlas does NOT:
- Perform precise call graph analysis
- Do type inference or dynamic dispatch resolution
- Expand C++ templates or macros

**Goal**: Usable but conservative index.

## Build

```bash
python -m nuitka --standalone --onefile --static-libpython=no \
  --include-package=codeatlas --output-dir=dist \
  codeatlas/__main__.py -o codeatlas
```

## License

MIT
