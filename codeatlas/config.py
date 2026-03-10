"""Configuration loading for CodeAtlas."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Default supported file extensions
SUPPORTED_EXTENSIONS: set[str] = {
    # Python
    ".py",
    ".pyi",
    # C/C++
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    # CUDA
    ".cu",
    ".cuh",
}


@dataclass
class TrustConfig:
    """Trust level configuration."""

    high: list[str] = field(default_factory=list)
    low: list[str] = field(default_factory=list)


@dataclass
class Config:
    """CodeAtlas configuration."""

    workspace: Path
    language: str
    entry: Optional[str]
    trust: TrustConfig
    exclude: list[str]
    extensions: set[str]

    @classmethod
    def load(cls, workspace: Path) -> "Config":
        config_path = workspace / ".codeatlas.yaml"
        if config_path.exists():
            return cls._load_from_file(config_path, workspace)
        return cls._default(workspace)

    @classmethod
    def _load_from_file(cls, config_path: Path, workspace: Path) -> "Config":
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        workspace_str = data.get("workspace", "./")
        if workspace_str == "./":
            resolved_workspace = workspace
        else:
            resolved_workspace = workspace / workspace_str

        trust_data = data.get("trust", {})
        trust = TrustConfig(
            high=trust_data.get("high", []),
            low=trust_data.get("low", []),
        )

        exclude = data.get("exclude", cls._default_exclude())

        # Load extensions from config or use default
        extensions_list = data.get("extensions")
        if extensions_list is not None:
            extensions = set(extensions_list)
        else:
            extensions = SUPPORTED_EXTENSIONS.copy()

        return cls(
            workspace=resolved_workspace.resolve(),
            language=data.get("language", "auto"),
            entry=data.get("entry"),
            trust=trust,
            exclude=exclude,
            extensions=extensions,
        )

    @classmethod
    def _default(cls, workspace: Path) -> "Config":
        return cls(
            workspace=workspace.resolve(),
            language="auto",
            entry=None,
            trust=TrustConfig(),
            exclude=cls._default_exclude(),
            extensions=SUPPORTED_EXTENSIONS.copy(),
        )

    @staticmethod
    def _default_exclude() -> list[str]:
        return [
            "__pycache__/",
            "*.pyc",
            ".git/",
            ".venv/",
            "venv/",
            "node_modules/",
            "build/",
            "dist/",
            ".eggs/",
            "*.egg-info/",
        ]

    def should_exclude(self, path: Path) -> bool:
        from fnmatch import fnmatch

        path_str = str(path)
        for pattern in self.exclude:
            if fnmatch(path_str, pattern):
                return True
            if fnmatch(path.name, pattern):
                return True
            for part in path.parts:
                if fnmatch(part, pattern.rstrip("/")):
                    return True
        return False

    def get_trust_level(self, path: str, in_workspace: bool) -> tuple[str, str]:
        for pattern in self.trust.high:
            if self._matches_trust_pattern(path, pattern):
                return ("high", "config")
        for pattern in self.trust.low:
            if self._matches_trust_pattern(path, pattern):
                return ("low", "config")

        if in_workspace:
            return ("low", "default")
        return ("high", "default")

    def _matches_trust_pattern(self, path: str, pattern: str) -> bool:
        from fnmatch import fnmatch

        if fnmatch(path, pattern):
            return True
        if fnmatch(path, f"*{pattern}*"):
            return True
        if pattern in path:
            return True
        return False
