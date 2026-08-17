from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PublishedConfig:
    content: bytes
    sha256: str
    modified_at: datetime
    metadata: dict[str, Any]


class AtomicPublisher:
    def __init__(self, data_dir: Path, *, retention_count: int):
        self.data_dir = data_dir
        self.current_path = data_dir / "current.yaml"
        self.metadata_path = data_dir / "metadata.json"
        self.versions_dir = data_dir / "versions"
        self.retention_count = retention_count

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.versions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        os.chmod(self.versions_dir, 0o700)

    def read_current(self) -> PublishedConfig | None:
        if not self.current_path.is_file():
            return None
        content = self.current_path.read_bytes()
        stat = self.current_path.stat()
        metadata: dict[str, Any] = {}
        if self.metadata_path.is_file():
            try:
                loaded = json.loads(self.metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    metadata = loaded
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                metadata = {}
        return PublishedConfig(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            metadata=metadata,
        )

    def publish(
        self, content: bytes, *, zod_node_count: int, route: str
    ) -> PublishedConfig:
        self.prepare()
        digest = hashlib.sha256(content).hexdigest()
        now = datetime.now(UTC)
        stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
        version_path = self.versions_dir / f"{stamp}-{digest[:12]}.yaml"

        self._atomic_write(version_path, content)
        self._atomic_write(self.current_path, content)
        metadata = {
            "last_success_at": now.isoformat(),
            "zod_node_count": zod_node_count,
            "route": route,
            "sha256": digest,
        }
        self._atomic_write(
            self.metadata_path,
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        self._prune_versions()
        return PublishedConfig(
            content=content, sha256=digest, modified_at=now, metadata=metadata
        )

    @staticmethod
    def _atomic_write(destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _prune_versions(self) -> None:
        versions = sorted(self.versions_dir.glob("*.yaml"), reverse=True)
        for stale in versions[self.retention_count :]:
            stale.unlink(missing_ok=True)
