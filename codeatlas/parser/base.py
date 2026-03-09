"""Base parser interface for CodeAtlas."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SymbolInfo:
    """Extracted symbol information from source code."""

    name: str
    qualified_name: str
    signature_hash: str
    symbol_kind: str
    start_line: int
    end_line: int
    parent_qualified_name: Optional[str]
    visibility: Optional[str]
    arity: Optional[int]
    is_definition: bool = True  # False for declarations (e.g., in .h files)


@dataclass
class EdgeInfo:
    """Extracted edge (connection) information between symbols."""

    src_qualified_name: str
    dst_qualified_name: str
    edge_kind: str


@dataclass
class ImportInfo:
    """Extracted import information."""

    module_path: str
    imported_names: list[str]
    is_from_import: bool


@dataclass
class ParseResult:
    """Result of parsing a source file."""

    file_path: str
    language: str
    symbols: list[SymbolInfo]
    edges: list[EdgeInfo]
    imports: list[ImportInfo]
    errors: list[str]


class BaseParser(ABC):
    """Abstract base class for language-specific parsers."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Return the language this parser handles."""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """Return file extensions this parser handles."""
        pass

    @abstractmethod
    def parse(self, file_path: Path, source: str) -> ParseResult:
        """Parse source code and extract symbols and edges.

        Args:
            file_path: Path to the source file (for module path computation)
            source: Source code content

        Returns:
            ParseResult containing extracted symbols and edges
        """
        pass

    @abstractmethod
    def compute_signature_hash(
        self,
        language: str,
        qualified_name: str,
        symbol_kind: str,
        arity: Optional[int],
    ) -> str:
        """Compute a stable signature hash for a symbol.

        Args:
            language: Programming language
            qualified_name: Fully qualified name of the symbol
            symbol_kind: Kind of symbol (function, class, etc.)
            arity: Number of parameters (for functions/methods)

        Returns:
            A stable hash string
        """
        pass

    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file."""
        return file_path.suffix in self.file_extensions
