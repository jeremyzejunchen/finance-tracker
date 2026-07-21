from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class StatementFile:
    relative_path: str
    account: str
    status: str
    sha256: str


class StatementDirectoryScanner:
    def __init__(self, root: Path, source_exists: Callable[[str], bool]):
        self.root = root.resolve()
        self.source_exists = source_exists

    def scan(self) -> list[StatementFile]:
        if not self.root.is_dir():
            return []
        files: list[StatementFile] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".csv":
                continue
            sha256 = self._sha256(path)
            account = self._account_for(path)
            status = "already_imported" if self.source_exists(sha256) else "ready" if account in {"ME", "WIFE"} else "needs_account_selection"
            files.append(StatementFile(path.relative_to(self.root).as_posix(), account, status, sha256))
        return files

    @staticmethod
    def _account_for(path: Path) -> str:
        name = path.name.casefold()
        if name.endswith("-czj.csv"):
            return "ME"
        if name.endswith("-cr.csv"):
            return "WIFE"
        return "needs_account_selection"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
