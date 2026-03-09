"""Trust domain management for CodeAtlas."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from ..storage.db import Database, TrustLogRecord


class TrustManager:
    """Manages trust levels for symbols and paths."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def set_trust(
        self,
        target: str,
        level: str,
    ) -> bool:
        """Set trust level for a symbol or path pattern.

        Args:
            target: Symbol #id, qualified name, or path pattern
            level: Trust level ("high" or "low")

        Returns:
            True if trust was updated, False if target not found
        """
        if level not in ("high", "low"):
            return False

        if target.startswith("#"):
            return self._set_symbol_trust_by_id(target, level)

        symbols = self._find_matching_symbols(target)
        if not symbols:
            return False

        for symbol_id, old_level in symbols:
            self._update_trust(symbol_id, target, old_level, level)

        return True

    def _set_symbol_trust_by_id(self, target: str, level: str) -> bool:
        """Set trust level by symbol ID."""
        try:
            symbol_id = int(target[1:])
        except ValueError:
            return False

        symbol = self.db.get_symbol_by_id(symbol_id)
        if not symbol:
            return False

        old_level = symbol.trust_level
        self._update_trust(symbol_id, target, old_level, level)
        return True

    def _find_matching_symbols(self, pattern: str) -> list[tuple[int, str]]:
        """Find symbols matching the pattern."""
        all_symbols = self.db.get_all_symbols()
        all_files = {f.id: f for f in self.db.get_all_files()}

        matches: list[tuple[int, str]] = []

        for symbol in all_symbols:
            if symbol.id is None:
                continue

            if pattern in symbol.qualified_name:
                matches.append((symbol.id, symbol.trust_level))
                continue

            file_record = all_files.get(symbol.file_id)
            if file_record and pattern in file_record.path:
                matches.append((symbol.id, symbol.trust_level))

        return matches

    def _update_trust(
        self,
        symbol_id: int,
        target: str,
        old_level: str,
        new_level: str,
    ) -> None:
        """Update trust level and log the change."""
        self.db.update_symbol_trust(symbol_id, new_level, "explicit")

        log_record = TrustLogRecord(
            id=None,
            timestamp=datetime.now().isoformat(),
            target=target,
            old_level=old_level,
            new_level=new_level,
            trust_source="explicit",
        )
        self.db.insert_trust_log(log_record)
