from __future__ import annotations

import ipaddress
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.utils import format_datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from app.config import Settings
from app.publisher import AtomicPublisher
from app.rate_limit import SlidingWindowRateLimiter
from app.refresher import RefreshService


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate()
    publisher = AtomicPublisher(
        resolved_settings.data_dir,
        retention_count=resolved_settings.retention_count,
    )
    service = RefreshService(resolved_settings, publisher)
    subscription_limiter = SlidingWindowRateLimiter(
        resolved_settings.subscription_rate_per_hour
    )
    admin_limiter = SlidingWindowRateLimiter(resolved_settings.admin_rate_per_hour)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        # httpx logs the complete request URL at INFO, including the private
        # subscription credential embedded in its path. Never allow it through.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        await service.initialize()
        scheduler: AsyncIOScheduler | None = None
        if resolved_settings.enable_scheduler:
            scheduler = AsyncIOScheduler(timezone="UTC")
            scheduler.add_job(
                service.trigger,
                CronTrigger(
                    hour=",".join(
                        str(hour) for hour in resolved_settings.schedule_hours_utc
                    ),
                    minute=0,
                    second=0,
                    timezone="UTC",
                ),
                args=["scheduled"],
                id="zod-refresh",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=1800,
            )
            scheduler.start()
        if resolved_settings.refresh_on_start:
            await service.trigger("startup")
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            await service.shutdown()

    app = FastAPI(
        title="LAZod Subscription Updater",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.publisher = publisher
    app.state.refresh_service = service

    def client_identity(request: Request) -> str:
        if resolved_settings.trust_cf_connecting_ip:
            forwarded = request.headers.get("CF-Connecting-IP", "").strip()
            try:
                if forwarded:
                    return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return request.client.host if request.client is not None else "unknown"

    async def require_admin(request: Request) -> None:
        if not await admin_limiter.allow(client_identity(request)):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {resolved_settings.admin_token}"
        if not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        current = publisher.read_current()
        if current is None:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/sub/{token}/LAZod.yaml", include_in_schema=False)
    async def subscription(token: str, request: Request) -> Response:
        if not await subscription_limiter.allow(client_identity(request)):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
        if not secrets.compare_digest(token, resolved_settings.subscription_token):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        current = publisher.read_current()
        if current is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(
            content=current.content,
            media_type="application/yaml",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": 'attachment; filename="LAZod.yaml"',
                "ETag": f'"{current.sha256}"',
                "Last-Modified": format_datetime(current.modified_at, usegmt=True),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/admin/refresh", include_in_schema=False)
    async def refresh(request: Request) -> JSONResponse:
        await require_admin(request)
        accepted = await service.trigger("manual")
        return JSONResponse(
            {"status": "accepted" if accepted else "already_running"},
            status_code=status.HTTP_202_ACCEPTED,
        )

    @app.get("/admin/status", include_in_schema=False)
    async def refresh_status(request: Request) -> JSONResponse:
        await require_admin(request)
        payload = await service.status()
        payload["has_config"] = publisher.read_current() is not None
        return JSONResponse(payload)

    return app
