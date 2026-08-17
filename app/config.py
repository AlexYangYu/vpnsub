from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    )


@dataclass(frozen=True, slots=True)
class Settings:
    upstream_url: str
    subscription_token: str
    admin_token: str
    base_config_path: Path = Path("/app/config/LAZod.yaml")
    data_dir: Path = Path("/data")
    mihomo_path: Path = Path("/usr/local/bin/mihomo")
    static_proxy_names: tuple[str, ...] = ("LA",)
    min_zod_nodes: int = 5
    fetch_timeout_seconds: int = 45
    fetch_attempts: int = 3
    mihomo_start_timeout_seconds: int = 15
    retention_count: int = 5
    user_agent: str = "clash.meta"
    schedule_hours_utc: tuple[int, ...] = (0, 6, 12, 18)
    enable_scheduler: bool = True
    refresh_on_start: bool = True
    trust_cf_connecting_ip: bool = True
    subscription_rate_per_hour: int = 60
    admin_rate_per_hour: int = 20
    allow_insecure_upstream: bool = False
    validate_with_mihomo: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        hours = tuple(
            sorted(
                {int(value) for value in _csv_env("SCHEDULE_HOURS_UTC", "0,6,12,18")}
            )
        )
        if not hours or any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError("SCHEDULE_HOURS_UTC must contain hours from 0 through 23")

        settings = cls(
            upstream_url=os.getenv("ZOD_SUBSCRIPTION_URL", "").strip(),
            subscription_token=os.getenv("SUBSCRIPTION_TOKEN", "").strip(),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            base_config_path=Path(
                os.getenv("BASE_CONFIG_PATH", "/app/config/LAZod.yaml")
            ),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            mihomo_path=Path(os.getenv("MIHOMO_PATH", "/usr/local/bin/mihomo")),
            static_proxy_names=_csv_env("STATIC_PROXY_NAMES", "LA"),
            min_zod_nodes=_int_env("MIN_ZOD_NODES", 5),
            fetch_timeout_seconds=_int_env("FETCH_TIMEOUT_SECONDS", 45),
            fetch_attempts=_int_env("FETCH_ATTEMPTS", 3),
            mihomo_start_timeout_seconds=_int_env("MIHOMO_START_TIMEOUT_SECONDS", 15),
            retention_count=_int_env("RETENTION_COUNT", 5),
            user_agent=os.getenv("UPSTREAM_USER_AGENT", "clash.meta").strip()
            or "clash.meta",
            schedule_hours_utc=hours,
            enable_scheduler=_bool_env("ENABLE_SCHEDULER", True),
            refresh_on_start=_bool_env("REFRESH_ON_START", True),
            trust_cf_connecting_ip=_bool_env("TRUST_CF_CONNECTING_IP", True),
            subscription_rate_per_hour=_int_env("SUBSCRIPTION_RATE_PER_HOUR", 60),
            admin_rate_per_hour=_int_env("ADMIN_RATE_PER_HOUR", 20),
            allow_insecure_upstream=_bool_env("ALLOW_INSECURE_UPSTREAM", False),
            validate_with_mihomo=_bool_env("VALIDATE_WITH_MIHOMO", True),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.upstream_url:
            raise ValueError("ZOD_SUBSCRIPTION_URL is required")
        if not self.allow_insecure_upstream and not self.upstream_url.startswith(
            "https://"
        ):
            raise ValueError("ZOD_SUBSCRIPTION_URL must use HTTPS")
        if len(self.subscription_token) < 32:
            raise ValueError("SUBSCRIPTION_TOKEN must be at least 32 characters")
        if len(self.admin_token) < 32:
            raise ValueError("ADMIN_TOKEN must be at least 32 characters")
        if self.subscription_token == self.admin_token:
            raise ValueError("SUBSCRIPTION_TOKEN and ADMIN_TOKEN must be different")
        if not self.static_proxy_names:
            raise ValueError("STATIC_PROXY_NAMES must contain at least one name")
