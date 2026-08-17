from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.merge import extract_update_candidates, filter_zod_proxies, merge_lazod
from app.mihomo import MihomoProxy, validate_config
from app.publisher import AtomicPublisher
from app.yaml_utils import SafeConfigError, dump_yaml, load_yaml_bytes

LOGGER = logging.getLogger("lazod.refresh")
MAX_SUBSCRIPTION_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class RefreshStatus:
    running: bool = False
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    zod_node_count: int = 0
    route: str | None = None
    output_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshResult:
    zod_node_count: int
    route: str
    sha256: str


class RefreshService:
    def __init__(self, settings: Settings, publisher: AtomicPublisher):
        self.settings = settings
        self.publisher = publisher
        self._status = RefreshStatus()
        self._state_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        self.publisher.prepare()
        current = self.publisher.read_current()
        if current is None:
            return
        metadata = current.metadata
        async with self._state_lock:
            self._status.last_success_at = _string_or_none(
                metadata.get("last_success_at")
            )
            self._status.zod_node_count = _int_or_zero(metadata.get("zod_node_count"))
            self._status.route = _string_or_none(metadata.get("route"))
            self._status.output_sha256 = current.sha256

    async def trigger(self, source: str) -> bool:
        async with self._state_lock:
            if self._task is not None and not self._task.done():
                return False
            self._task = asyncio.create_task(
                self._run(source), name=f"lazod-refresh-{source}"
            )
            return True

    async def status(self) -> dict[str, Any]:
        async with self._state_lock:
            return asdict(self._status)

    async def shutdown(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self, source: str) -> None:
        attempt_at = datetime.now(UTC).isoformat()
        async with self._state_lock:
            self._status.running = True
            self._status.last_attempt_at = attempt_at
            self._status.last_error = None
        LOGGER.info("refresh_started source=%s", source)

        try:
            result = await self.refresh_once()
        except asyncio.CancelledError:
            async with self._state_lock:
                self._status.running = False
                self._status.last_error = "cancelled"
            raise
        except SafeConfigError as exc:
            async with self._state_lock:
                self._status.running = False
                self._status.last_error = exc.code
            LOGGER.warning("refresh_failed code=%s", exc.code)
        # This task boundary must retain the last-good file after unexpected failures.
        # Never log exception text because it can contain the secret upstream URL.
        except Exception as exc:  # noqa: BLE001
            async with self._state_lock:
                self._status.running = False
                self._status.last_error = "internal_error"
            LOGGER.error(
                "refresh_failed code=internal_error type=%s", type(exc).__name__
            )
        else:
            success_at = datetime.now(UTC).isoformat()
            async with self._state_lock:
                self._status.running = False
                self._status.last_success_at = success_at
                self._status.last_error = None
                self._status.zod_node_count = result.zod_node_count
                self._status.route = result.route
                self._status.output_sha256 = result.sha256
            LOGGER.info(
                "refresh_succeeded route=%s zod_nodes=%d sha256=%s",
                result.route,
                result.zod_node_count,
                result.sha256,
            )

    async def refresh_once(self) -> RefreshResult:
        try:
            base_payload = self.settings.base_config_path.read_bytes()
        except OSError as exc:
            raise SafeConfigError(
                "base_read_failed", "base configuration could not be read"
            ) from exc
        base = load_yaml_bytes(base_payload, source="base configuration")

        bootstrap_payload = await self._download(proxy_url=None)
        bootstrap = load_yaml_bytes(bootstrap_payload, source="bootstrap subscription")
        candidates = extract_update_candidates(bootstrap)

        failure_codes: list[str] = []
        for candidate in candidates:
            candidate_type = str(candidate.get("type", "")).lower()
            route = "AN" if candidate_type == "anytls" else "VL"
            try:
                async with MihomoProxy(
                    self.settings.mihomo_path,
                    candidate,
                    start_timeout=self.settings.mihomo_start_timeout_seconds,
                ) as proxy:
                    updated_payload = await self._download(proxy_url=proxy.proxy_url)
                updated = load_yaml_bytes(
                    updated_payload, source="updated subscription"
                )
                zod_proxies = filter_zod_proxies(
                    updated, minimum=self.settings.min_zod_nodes
                )
                merged, node_count = merge_lazod(
                    base,
                    zod_proxies,
                    static_proxy_names=self.settings.static_proxy_names,
                )
                config_text = dump_yaml(merged)
                if self.settings.validate_with_mihomo:
                    await validate_config(self.settings.mihomo_path, config_text)
                published = self.publisher.publish(
                    config_text.encode("utf-8"),
                    zod_node_count=node_count,
                    route=route,
                )
                return RefreshResult(node_count, route, published.sha256)
            except asyncio.CancelledError:
                raise
            except SafeConfigError as exc:
                failure_codes.append(exc.code)
                continue

        code = failure_codes[-1] if failure_codes else "all_update_nodes_failed"
        raise SafeConfigError(
            "all_update_nodes_failed", f"all update nodes failed ({code})"
        )

    async def _download(self, *, proxy_url: str | None) -> bytes:
        last_code = "upstream_request_failed"
        for attempt in range(self.settings.fetch_attempts):
            try:
                timeout = httpx.Timeout(self.settings.fetch_timeout_seconds)
                async with (
                    httpx.AsyncClient(
                        proxy=proxy_url,
                        timeout=timeout,
                        follow_redirects=True,
                        headers={
                            "User-Agent": self.settings.user_agent,
                            "Accept": "application/yaml,text/yaml,*/*",
                        },
                    ) as client,
                    client.stream("GET", self.settings.upstream_url) as response,
                ):
                    if response.status_code < 200 or response.status_code >= 300:
                        last_code = "upstream_http_error"
                        raise SafeConfigError(
                            last_code, "upstream returned a non-success status"
                        )
                    if (
                        not self.settings.allow_insecure_upstream
                        and response.url.scheme != "https"
                    ):
                        raise SafeConfigError(
                            "insecure_redirect",
                            "upstream redirected away from HTTPS",
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_SUBSCRIPTION_BYTES:
                            raise SafeConfigError(
                                "upstream_too_large",
                                "upstream response is too large",
                            )
                    return bytes(content)
            except SafeConfigError as exc:
                last_code = exc.code
            except httpx.TimeoutException:
                last_code = "upstream_timeout"
            except httpx.HTTPError:
                last_code = "upstream_request_failed"
            if attempt + 1 < self.settings.fetch_attempts:
                await asyncio.sleep(2**attempt)
        raise SafeConfigError(last_code, "upstream request failed")


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
