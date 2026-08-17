from __future__ import annotations

import hashlib
import stat

from app.publisher import AtomicPublisher


def test_atomic_publish_and_retention(tmp_path) -> None:
    publisher = AtomicPublisher(tmp_path / "data", retention_count=5)
    for index in range(7):
        publisher.publish(
            f"version: {index}\n".encode(),
            zod_node_count=index,
            route="AN",
        )

    current = publisher.read_current()
    assert current is not None
    assert current.content == b"version: 6\n"
    assert current.sha256 == hashlib.sha256(current.content).hexdigest()
    assert current.metadata["zod_node_count"] == 6
    assert len(list((tmp_path / "data" / "versions").glob("*.yaml"))) == 5
    assert stat.S_IMODE((tmp_path / "data" / "current.yaml").stat().st_mode) == 0o600
