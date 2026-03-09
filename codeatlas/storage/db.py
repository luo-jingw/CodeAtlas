"""SQLite database operations for CodeAtlas."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class FileRecord:
    id: Optional[int]
    path: str
    parent_dir: str
    language: str
    in_workspace: bool
    content_hash: str
    parse_status: str
    last_indexed_at: str


@dataclass
class SymbolRecord:
    id: Optional[int]
    name: str
    qualified_name: str
    signature_hash: str
    symbol_kind: str
    language: str
    file_id: int
    parent_symbol_id: Optional[int]
    decl_file_id: int
    decl_start: int
    decl_end: int
    def_file_id: int
    def_start: int
    def_end: int
    trust_level: str
    trust_source: str
    visibility: Optional[str]
    symbol_status: Optional[str]  # "declaration_only", "definition_only", or None
    updated_at: str


@dataclass
class EdgeRecord:
    id: Optional[int]
    src_symbol_id: int
    dst_symbol_id: int
    edge_kind: str


@dataclass
class FileEdgeRecord:
    id: Optional[int]
    src_file_id: int
    dst_file_id: int
    edge_kind: str


@dataclass
class TrustLogRecord:
    id: Optional[int]
    timestamp: str
    target: str
    old_level: str
    new_level: str
    trust_source: str


@dataclass
class NamespaceRecord:
    id: Optional[int]
    name: str
    qualified_name: str
    parent_namespace_id: Optional[int]


class Database:
    """SQLite database wrapper for CodeAtlas."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                parent_dir TEXT,
                language TEXT,
                in_workspace INTEGER,
                content_hash TEXT,
                parse_status TEXT,
                last_indexed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                name TEXT,
                qualified_name TEXT,
                signature_hash TEXT,
                symbol_kind TEXT,
                language TEXT,
                file_id INTEGER,
                parent_symbol_id INTEGER,
                decl_file_id INTEGER,
                decl_start INTEGER,
                decl_end INTEGER,
                def_file_id INTEGER,
                def_start INTEGER,
                def_end INTEGER,
                trust_level TEXT,
                trust_source TEXT,
                visibility TEXT,
                symbol_status TEXT,
                updated_at TEXT,
                FOREIGN KEY (file_id) REFERENCES files(id),
                FOREIGN KEY (parent_symbol_id) REFERENCES symbols(id),
                FOREIGN KEY (decl_file_id) REFERENCES files(id),
                FOREIGN KEY (def_file_id) REFERENCES files(id)
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY,
                src_symbol_id INTEGER,
                dst_symbol_id INTEGER,
                edge_kind TEXT,
                FOREIGN KEY (src_symbol_id) REFERENCES symbols(id),
                FOREIGN KEY (dst_symbol_id) REFERENCES symbols(id)
            );

            CREATE TABLE IF NOT EXISTS file_edges (
                id INTEGER PRIMARY KEY,
                src_file_id INTEGER,
                dst_file_id INTEGER,
                edge_kind TEXT,
                FOREIGN KEY (src_file_id) REFERENCES files(id),
                FOREIGN KEY (dst_file_id) REFERENCES files(id)
            );

            CREATE TABLE IF NOT EXISTS namespaces (
                id INTEGER PRIMARY KEY,
                name TEXT,
                qualified_name TEXT,
                parent_namespace_id INTEGER,
                FOREIGN KEY (parent_namespace_id) REFERENCES namespaces(id)
            );

            CREATE TABLE IF NOT EXISTS namespace_members (
                namespace_id INTEGER,
                symbol_id INTEGER,
                PRIMARY KEY (namespace_id, symbol_id),
                FOREIGN KEY (namespace_id) REFERENCES namespaces(id),
                FOREIGN KEY (symbol_id) REFERENCES symbols(id)
            );

            CREATE TABLE IF NOT EXISTS trust_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                target TEXT,
                old_level TEXT,
                new_level TEXT,
                trust_source TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
            CREATE INDEX IF NOT EXISTS idx_files_parent_dir ON files(parent_dir);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_signature_hash ON symbols(signature_hash);
            CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
            CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_symbol_id);
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_symbol_id);
            CREATE INDEX IF NOT EXISTS idx_file_edges_src ON file_edges(src_file_id);
            CREATE INDEX IF NOT EXISTS idx_file_edges_dst ON file_edges(dst_file_id);

            -- Unique constraints to prevent duplicate edges
            CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
                ON edges(src_symbol_id, dst_symbol_id, edge_kind);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_file_edges_unique
                ON file_edges(src_file_id, dst_file_id, edge_kind);
        """)
        self.conn.commit()

    def insert_file(self, record: FileRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            """
            INSERT INTO files (path, parent_dir, language, in_workspace, content_hash, parse_status, last_indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.path,
                record.parent_dir,
                record.language,
                1 if record.in_workspace else 0,
                record.content_hash,
                record.parse_status,
                record.last_indexed_at,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore

    def get_file_by_path(self, path: str) -> Optional[FileRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM files WHERE path = ?", (path,))
        row = cursor.fetchone()
        if row is None:
            return None
        return FileRecord(
            id=row["id"],
            path=row["path"],
            parent_dir=row["parent_dir"],
            language=row["language"],
            in_workspace=bool(row["in_workspace"]),
            content_hash=row["content_hash"],
            parse_status=row["parse_status"],
            last_indexed_at=row["last_indexed_at"],
        )

    def update_file(self, record: FileRecord) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")
        if record.id is None:
            raise ValueError("Cannot update file without id")

        self.conn.execute(
            """
            UPDATE files SET
                path = ?, parent_dir = ?, language = ?, in_workspace = ?,
                content_hash = ?, parse_status = ?, last_indexed_at = ?
            WHERE id = ?
            """,
            (
                record.path,
                record.parent_dir,
                record.language,
                1 if record.in_workspace else 0,
                record.content_hash,
                record.parse_status,
                record.last_indexed_at,
                record.id,
            ),
        )
        self.conn.commit()

    def insert_symbol(self, record: SymbolRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            """
            INSERT INTO symbols (
                name, qualified_name, signature_hash, symbol_kind, language,
                file_id, parent_symbol_id, decl_file_id, decl_start, decl_end,
                def_file_id, def_start, def_end, trust_level, trust_source,
                visibility, symbol_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.name,
                record.qualified_name,
                record.signature_hash,
                record.symbol_kind,
                record.language,
                record.file_id,
                record.parent_symbol_id,
                record.decl_file_id,
                record.decl_start,
                record.decl_end,
                record.def_file_id,
                record.def_start,
                record.def_end,
                record.trust_level,
                record.trust_source,
                record.visibility,
                record.symbol_status,
                record.updated_at,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore

    def get_symbol_by_signature_hash(self, signature_hash: str) -> Optional[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM symbols WHERE signature_hash = ?", (signature_hash,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_symbol(row)

    def get_symbols_by_file_id(self, file_id: int) -> list[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM symbols WHERE file_id = ?", (file_id,)
        )
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def _row_to_symbol(self, row: sqlite3.Row) -> SymbolRecord:
        return SymbolRecord(
            id=row["id"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            signature_hash=row["signature_hash"],
            symbol_kind=row["symbol_kind"],
            language=row["language"],
            file_id=row["file_id"],
            parent_symbol_id=row["parent_symbol_id"],
            decl_file_id=row["decl_file_id"],
            decl_start=row["decl_start"],
            decl_end=row["decl_end"],
            def_file_id=row["def_file_id"],
            def_start=row["def_start"],
            def_end=row["def_end"],
            trust_level=row["trust_level"],
            trust_source=row["trust_source"],
            visibility=row["visibility"],
            symbol_status=row["symbol_status"] if "symbol_status" in row.keys() else None,
            updated_at=row["updated_at"],
        )

    def update_symbol_trust(
        self, symbol_id: int, trust_level: str, trust_source: str
    ) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            "UPDATE symbols SET trust_level = ?, trust_source = ? WHERE id = ?",
            (trust_level, trust_source, symbol_id),
        )
        self.conn.commit()

    def delete_symbols_by_file_id(self, file_id: int) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        self.conn.commit()

    def insert_edge(self, record: EdgeRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO edges (src_symbol_id, dst_symbol_id, edge_kind) VALUES (?, ?, ?)",
            (record.src_symbol_id, record.dst_symbol_id, record.edge_kind),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_edges_from_symbol(self, symbol_id: int) -> list[EdgeRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM edges WHERE src_symbol_id = ?", (symbol_id,)
        )
        return [
            EdgeRecord(
                id=row["id"],
                src_symbol_id=row["src_symbol_id"],
                dst_symbol_id=row["dst_symbol_id"],
                edge_kind=row["edge_kind"],
            )
            for row in cursor.fetchall()
        ]

    def get_edges_to_symbol(self, symbol_id: int) -> list[EdgeRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM edges WHERE dst_symbol_id = ?", (symbol_id,)
        )
        return [
            EdgeRecord(
                id=row["id"],
                src_symbol_id=row["src_symbol_id"],
                dst_symbol_id=row["dst_symbol_id"],
                edge_kind=row["edge_kind"],
            )
            for row in cursor.fetchall()
        ]

    def delete_edges_by_symbol_id(self, symbol_id: int) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            "DELETE FROM edges WHERE src_symbol_id = ? OR dst_symbol_id = ?",
            (symbol_id, symbol_id),
        )
        self.conn.commit()

    def insert_file_edge(self, record: FileEdgeRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO file_edges (src_file_id, dst_file_id, edge_kind) VALUES (?, ?, ?)",
            (record.src_file_id, record.dst_file_id, record.edge_kind),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore

    def get_file_edges_from_file(self, file_id: int) -> list[FileEdgeRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM file_edges WHERE src_file_id = ?", (file_id,)
        )
        return [
            FileEdgeRecord(
                id=row["id"],
                src_file_id=row["src_file_id"],
                dst_file_id=row["dst_file_id"],
                edge_kind=row["edge_kind"],
            )
            for row in cursor.fetchall()
        ]

    def delete_file_edges_by_file_id(self, file_id: int) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            "DELETE FROM file_edges WHERE src_file_id = ? OR dst_file_id = ?",
            (file_id, file_id),
        )
        self.conn.commit()

    def insert_trust_log(self, record: TrustLogRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            """
            INSERT INTO trust_log (timestamp, target, old_level, new_level, trust_source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.timestamp,
                record.target,
                record.old_level,
                record.new_level,
                record.trust_source,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore

    def get_all_files(self) -> list[FileRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM files")
        return [
            FileRecord(
                id=row["id"],
                path=row["path"],
                parent_dir=row["parent_dir"],
                language=row["language"],
                in_workspace=bool(row["in_workspace"]),
                content_hash=row["content_hash"],
                parse_status=row["parse_status"],
                last_indexed_at=row["last_indexed_at"],
            )
            for row in cursor.fetchall()
        ]

    def search_symbols(
        self,
        query: str,
        exact: bool = False,
        kind: Optional[str] = None,
        path: Optional[str] = None,
    ) -> list[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        conditions = []
        params: list[str] = []

        if exact:
            conditions.append("name = ?")
            params.append(query)
        else:
            conditions.append("name LIKE ?")
            params.append(f"%{query}%")

        if kind:
            conditions.append("symbol_kind = ?")
            params.append(kind)

        sql = f"SELECT * FROM symbols WHERE {' AND '.join(conditions)}"

        if path:
            sql = f"""
                SELECT s.* FROM symbols s
                JOIN files f ON s.file_id = f.id
                WHERE {' AND '.join(conditions)} AND f.path LIKE ?
            """
            params.append(f"%{path}%")

        cursor = self.conn.execute(sql, params)
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def get_symbol_by_id(self, symbol_id: int) -> Optional[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_symbol(row)

    def get_file_by_id(self, file_id: int) -> Optional[FileRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return FileRecord(
            id=row["id"],
            path=row["path"],
            parent_dir=row["parent_dir"],
            language=row["language"],
            in_workspace=bool(row["in_workspace"]),
            content_hash=row["content_hash"],
            parse_status=row["parse_status"],
            last_indexed_at=row["last_indexed_at"],
        )

    def get_all_file_edges(self) -> list[FileEdgeRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM file_edges")
        return [
            FileEdgeRecord(
                id=row["id"],
                src_file_id=row["src_file_id"],
                dst_file_id=row["dst_file_id"],
                edge_kind=row["edge_kind"],
            )
            for row in cursor.fetchall()
        ]

    def get_all_symbols(self) -> list[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM symbols")
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def get_all_edges(self) -> list[EdgeRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute("SELECT * FROM edges")
        return [
            EdgeRecord(
                id=row["id"],
                src_symbol_id=row["src_symbol_id"],
                dst_symbol_id=row["dst_symbol_id"],
                edge_kind=row["edge_kind"],
            )
            for row in cursor.fetchall()
        ]

    # Namespace operations

    def insert_namespace(self, record: NamespaceRecord) -> int:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            """
            INSERT INTO namespaces (name, qualified_name, parent_namespace_id)
            VALUES (?, ?, ?)
            """,
            (record.name, record.qualified_name, record.parent_namespace_id),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore

    def get_namespace_by_qualified_name(
        self, qualified_name: str
    ) -> Optional[NamespaceRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            "SELECT * FROM namespaces WHERE qualified_name = ?", (qualified_name,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return NamespaceRecord(
            id=row["id"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            parent_namespace_id=row["parent_namespace_id"],
        )

    def insert_namespace_member(self, namespace_id: int, symbol_id: int) -> None:
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            "INSERT OR IGNORE INTO namespace_members (namespace_id, symbol_id) VALUES (?, ?)",
            (namespace_id, symbol_id),
        )
        self.conn.commit()

    def get_namespace_members(self, namespace_id: int) -> list[SymbolRecord]:
        if not self.conn:
            raise RuntimeError("Database not connected")

        cursor = self.conn.execute(
            """
            SELECT s.* FROM symbols s
            JOIN namespace_members nm ON s.id = nm.symbol_id
            WHERE nm.namespace_id = ?
            """,
            (namespace_id,),
        )
        return [self._row_to_symbol(row) for row in cursor.fetchall()]

    def clear_namespaces(self) -> None:
        """Clear all namespaces and namespace_members."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute("DELETE FROM namespace_members")
        self.conn.execute("DELETE FROM namespaces")
        self.conn.commit()

    def delete_namespace_members_by_symbol_id(self, symbol_id: int) -> None:
        """Delete namespace_members entries for a symbol."""
        if not self.conn:
            raise RuntimeError("Database not connected")

        self.conn.execute(
            "DELETE FROM namespace_members WHERE symbol_id = ?", (symbol_id,)
        )
        self.conn.commit()
