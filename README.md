# CodeAtlas

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-green)

LLM-oriented code atlas for understanding code structure with minimal tokens.

## When to Use

- LLM 需要快速理解陌生代码库结构
- 定位符号定义、查看依赖关系
- 生成代码前了解现有接口和模块边界

**Core principle**: 辅助工具，不参与构建/版本控制/测试。索引可随时从源码重建。

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
# 1. 初始化索引
codeatlas init

# 2. 查看模块依赖（C++ 扁平目录建议用 logic 视图）
codeatlas overview
codeatlas overview --view logic

# 3. 搜索符号
codeatlas search Manager
# Output: #42 [src/manager.py:15-89] Manager  [low]

# 4. 查看符号详情和连接关系
codeatlas inspect "#42" --level 2
codeatlas trace "#42"

# 5. 读取源码
codeatlas read "#42"
```

## Command Reference

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `init` | 初始化索引 | `--workspace PATH` |
| `overview` | 模块依赖图 | `--view file\|logic`, `--filter PATH` |
| `inspect` | 符号详情 | `--level 1\|2`, `--detail method` |
| `search` | 符号搜索 | `--exact`, `--kind`, `--path` |
| `read` | 读取源码 | `--part declaration\|definition` |
| `trace` | 连接关系 | `--direction forward\|backward\|both` |
| `trust` | 设置信任等级 | `high\|low` |
| `rebuild` | 重建索引 | `[path]` 支持单文件/目录 |

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

- **扁平目录结构**：使用 `--view logic` 基于 namespace/class 聚合
- **Include 记录**：仅 `#include "..."` 记录 file_edges，`#include <...>` 不记录
- **声明/定义合并**：.h 声明与 .cpp 定义通过 signature_hash 自动合并
- **前向声明**：`class Foo;` 不创建符号，避免重复

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

为最大化 CodeAtlas 效果，建议代码遵循：

1. **显式依赖** - 无通配符导入，无动态导入
2. **单一职责** - 一个文件做一件事
3. **接口分离** - C++ .h/.cpp 分离，Python 用 Protocol/ABC
4. **完整类型标注** - 函数签名写全类型
5. **避免隐式模式** - 无 metaclass 动态生成、无 `__getattr__` 代理

**Summary: Make everything explicit.** 详见 `codeatlas --help`

## Architecture

```
codeatlas/
  cli.py              # Entry point
  config.py           # Configuration
  indexer.py          # Parse coordination
  parser/             # Language parsers (Python, C++)
  storage/db.py       # SQLite operations
  query/              # overview, inspect, search, read, trace
  trust/manager.py    # Trust level management
  render/compact.py   # Output formatting
```

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
