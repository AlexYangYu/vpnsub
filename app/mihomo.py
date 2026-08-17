from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from app.yaml_utils import SafeConfigError, dump_yaml


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MihomoProxy:
    def __init__(self, binary: Path, proxy: dict[str, Any], *, start_timeout: int):
        self.binary = binary
        self.proxy = proxy
        self.start_timeout = start_timeout
        self.port = _available_port()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._process: asyncio.subprocess.Process | None = None

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def __aenter__(self) -> Self:
        if not self.binary.is_file():
            raise SafeConfigError("mihomo_missing", "Mihomo binary is not available")

        self._temporary = tempfile.TemporaryDirectory(prefix="lazod-mihomo-")
        workdir = Path(self._temporary.name)
        proxy_name = str(self.proxy.get("name", "UPDATE-NODE"))
        config = {
            "port": self.port,
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "mode": "rule",
            "log-level": "silent",
            "ipv6": False,
            "proxies": [self.proxy],
            "proxy-groups": [
                {
                    "name": "UPDATE",
                    "type": "select",
                    "proxies": [proxy_name],
                }
            ],
            "rules": ["MATCH,UPDATE"],
        }
        config_path = workdir / "config.yaml"
        config_path.write_text(dump_yaml(config), encoding="utf-8")
        os.chmod(config_path, 0o600)

        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self.binary),
                "-d",
                str(workdir),
                "-f",
                str(config_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            self._cleanup_tempdir()
            raise SafeConfigError(
                "mihomo_start_failed", "Mihomo could not be started"
            ) from exc

        try:
            await asyncio.wait_for(self._wait_until_ready(), timeout=self.start_timeout)
        except asyncio.CancelledError:
            await self._stop()
            self._cleanup_tempdir()
            raise
        except (TimeoutError, SafeConfigError):
            await self._stop()
            self._cleanup_tempdir()
            raise SafeConfigError("mihomo_not_ready", "Mihomo did not become ready")
        return self

    async def _wait_until_ready(self) -> None:
        while True:
            if self._process is None or self._process.returncode is not None:
                raise SafeConfigError(
                    "mihomo_exited", "Mihomo exited before becoming ready"
                )
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                await writer.wait_closed()
                del reader
                return
            except OSError:
                await asyncio.sleep(0.1)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self._stop()
        self._cleanup_tempdir()

    async def _stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()

    def _cleanup_tempdir(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


async def validate_config(binary: Path, config_text: str) -> None:
    if not binary.is_file():
        raise SafeConfigError("mihomo_missing", "Mihomo binary is not available")

    with tempfile.TemporaryDirectory(prefix="lazod-validate-") as temporary:
        workdir = Path(temporary)
        config_path = workdir / "LAZod.yaml"
        config_path.write_text(config_text, encoding="utf-8")
        os.chmod(config_path, 0o600)
        try:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "-t",
                "-d",
                str(workdir),
                "-f",
                str(config_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise SafeConfigError(
                "mihomo_validation_failed", "Mihomo validation could not complete"
            ) from exc
        try:
            return_code = await asyncio.wait_for(process.wait(), timeout=45)
        except (TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SafeConfigError(
                "mihomo_validation_failed", "Mihomo validation could not complete"
            ) from exc
        if return_code != 0:
            raise SafeConfigError(
                "mihomo_validation_failed",
                "Mihomo rejected the generated configuration",
            )
