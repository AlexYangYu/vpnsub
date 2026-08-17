from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.publisher import AtomicPublisher
from app.refresher import RefreshResult, RefreshService
from app.yaml_utils import SafeConfigError, dump_yaml, load_yaml_bytes


class FakeMihomoProxy:
    def __init__(self, binary: Path, proxy: dict, *, start_timeout: int):
        del binary, start_timeout
        self.proxy_url = f"http://{proxy['type']}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


def make_settings(tmp_path, base_path: Path) -> Settings:
    return Settings(
        upstream_url="https://subscription.example.invalid/zod",
        subscription_token="s" * 64,
        admin_token="a" * 64,
        base_config_path=base_path,
        data_dir=tmp_path / "data",
        mihomo_path=tmp_path / "mihomo",
        min_zod_nodes=5,
        fetch_attempts=1,
        enable_scheduler=False,
        refresh_on_start=False,
        validate_with_mihomo=False,
    )


@pytest.mark.asyncio
async def test_refresh_falls_back_from_anytls_to_vless(
    tmp_path,
    monkeypatch,
    base_config: dict,
    bootstrap_config: dict,
    updated_config: dict,
) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text(dump_yaml(base_config), encoding="utf-8")
    settings = make_settings(tmp_path, base_path)
    publisher = AtomicPublisher(settings.data_dir, retention_count=5)
    service = RefreshService(settings, publisher)

    async def fake_download(*, proxy_url: str | None) -> bytes:
        if proxy_url is None or proxy_url.endswith("anytls"):
            return dump_yaml(bootstrap_config).encode()
        return dump_yaml(updated_config).encode()

    monkeypatch.setattr("app.refresher.MihomoProxy", FakeMihomoProxy)
    monkeypatch.setattr(service, "_download", fake_download)

    result = await service.refresh_once()
    assert result.route == "VL"
    assert result.zod_node_count == 6
    current = publisher.read_current()
    assert current is not None
    generated = load_yaml_bytes(current.content, source="generated")
    names = {proxy["name"] for proxy in generated["proxies"]}
    assert "LA" in names
    assert "PRO | 订阅专用节点AN | 0倍" not in names


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_good(
    tmp_path,
    monkeypatch,
    base_config: dict,
    bootstrap_config: dict,
) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text(dump_yaml(base_config), encoding="utf-8")
    settings = make_settings(tmp_path, base_path)
    publisher = AtomicPublisher(settings.data_dir, retention_count=5)
    seed = publisher.publish(b"last-good: true\n", zod_node_count=10, route="AN")
    service = RefreshService(settings, publisher)

    async def fake_download(*, proxy_url: str | None) -> bytes:
        del proxy_url
        return dump_yaml(bootstrap_config).encode()

    monkeypatch.setattr("app.refresher.MihomoProxy", FakeMihomoProxy)
    monkeypatch.setattr(service, "_download", fake_download)

    with pytest.raises(SafeConfigError):
        await service.refresh_once()
    current = publisher.read_current()
    assert current is not None
    assert current.sha256 == seed.sha256


@pytest.mark.asyncio
async def test_trigger_is_single_flight(
    tmp_path, monkeypatch, base_config: dict
) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text(dump_yaml(base_config), encoding="utf-8")
    settings = make_settings(tmp_path, base_path)
    service = RefreshService(
        settings, AtomicPublisher(settings.data_dir, retention_count=5)
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_refresh() -> RefreshResult:
        started.set()
        await release.wait()
        return RefreshResult(5, "AN", "f" * 64)

    monkeypatch.setattr(service, "refresh_once", blocked_refresh)
    assert await service.trigger("first") is True
    await started.wait()
    assert await service.trigger("second") is False
    release.set()
    assert service._task is not None
    await service._task
    status = await service.status()
    assert status["running"] is False
    assert status["output_sha256"] == "f" * 64
