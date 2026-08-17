from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_auth_subscription_headers_and_health(tmp_path, monkeypatch) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text("proxies: []\nproxy-groups: []\nrules: []\n", encoding="utf-8")
    subscription_token = "s" * 64
    admin_token = "a" * 64
    settings = Settings(
        upstream_url="https://subscription.example.invalid/zod",
        subscription_token=subscription_token,
        admin_token=admin_token,
        base_config_path=base_path,
        data_dir=tmp_path / "data",
        mihomo_path=tmp_path / "mihomo",
        enable_scheduler=False,
        refresh_on_start=False,
        validate_with_mihomo=False,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        app.state.publisher.publish(
            b"mode: rule\n",
            zod_node_count=5,
            route="AN",
        )
        monkeypatch.setattr(
            app.state.refresh_service,
            "trigger",
            AsyncMock(return_value=True),
        )
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200

            missing = await client.get("/sub/wrong/LAZod.yaml")
            assert missing.status_code == 404

            subscription = await client.get(f"/sub/{subscription_token}/LAZod.yaml")
            assert subscription.status_code == 200
            assert subscription.content == b"mode: rule\n"
            assert subscription.headers["cache-control"] == "private, no-store"
            assert (
                subscription.headers["content-disposition"]
                == 'attachment; filename="LAZod.yaml"'
            )

            unauthorized = await client.get("/admin/status")
            assert unauthorized.status_code == 401

            headers = {"Authorization": f"Bearer {admin_token}"}
            status_response = await client.get("/admin/status", headers=headers)
            assert status_response.status_code == 200
            assert status_response.json()["has_config"] is True

            refresh = await client.post("/admin/refresh", headers=headers)
            assert refresh.status_code == 202
            assert refresh.json() == {"status": "accepted"}
