from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from app.yaml_utils import SafeConfigError

UPDATE_NODE_NAMES = (
    "PRO | 订阅专用节点AN | 0倍",
    "PRO | 订阅专用节点VL | 0倍",
)

INFO_NAME_FRAGMENTS = (
    "如发现大部分节点无法连接",
    "订阅信息过期需要更新订阅",
    "节点变动日期",
    "只看到我和订阅专用节点",
    "请连接订阅专用节点",
    "订阅专用节点",
    "通过代理更新没节点",
)

REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "USA": ("美国",),
    "HKG": ("香港",),
    "TWN": ("台湾", "台灣"),
    "JPN": ("日本",),
    "SGP": ("新加坡",),
}

REGION_GROUP_TO_REGION = {
    "USA": "USA",
    "USA Auto": "USA",
    "HKG": "HKG",
    "HKG Auto": "HKG",
    "TWN": "TWN",
    "TWN Auto": "TWN",
    "JPN": "JPN",
    "JPN Auto": "JPN",
    "SGP": "SGP",
    "SGP Auto": "SGP",
}

DEFAULT_REGION_POOL_ORDER = (
    "USA",
    "TWN",
    "TWN Auto",
    "SGP",
    "SGP Auto",
    "HKG",
    "HKG Auto",
    "JPN",
    "JPN Auto",
)

BUILTIN_OUTBOUNDS = {
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "PASS",
    "GLOBAL",
    "COMPATIBLE",
}


def _proxy_name(proxy: Any) -> str:
    if not isinstance(proxy, dict):
        return ""
    name = proxy.get("name")
    return name.strip() if isinstance(name, str) else ""


def is_information_proxy(proxy: Any) -> bool:
    name = _proxy_name(proxy)
    return not name or any(fragment in name for fragment in INFO_NAME_FRAGMENTS)


def extract_update_candidates(config: dict[str, Any]) -> list[dict[str, Any]]:
    proxies = config.get("proxies")
    if not isinstance(proxies, list):
        raise SafeConfigError(
            "missing_proxies", "bootstrap subscription has no proxy list"
        )

    by_name = {_proxy_name(proxy): proxy for proxy in proxies if _proxy_name(proxy)}
    candidates: list[dict[str, Any]] = []
    expected_types = {
        UPDATE_NODE_NAMES[0]: "anytls",
        UPDATE_NODE_NAMES[1]: "vless",
    }
    for name in UPDATE_NODE_NAMES:
        proxy = by_name.get(name)
        if not isinstance(proxy, dict):
            continue
        if str(proxy.get("type", "")).lower() != expected_types[name]:
            continue
        candidates.append(deepcopy(proxy))

    if not candidates:
        raise SafeConfigError(
            "missing_update_nodes",
            "bootstrap subscription has no supported update nodes",
        )
    return candidates


def filter_zod_proxies(config: dict[str, Any], *, minimum: int) -> list[dict[str, Any]]:
    proxies = config.get("proxies")
    if not isinstance(proxies, list):
        raise SafeConfigError(
            "missing_proxies", "updated subscription has no proxy list"
        )

    usable = [deepcopy(proxy) for proxy in proxies if not is_information_proxy(proxy)]
    if len(usable) < minimum:
        raise SafeConfigError(
            "too_few_nodes",
            f"updated subscription contains fewer than {minimum} usable nodes",
        )
    if not any(not _proxy_name(proxy).startswith("V0-") for proxy in usable):
        raise SafeConfigError(
            "bootstrap_only", "updated subscription is still in bootstrap state"
        )
    return usable


def _deduplicate_zod_names(
    proxies: Iterable[dict[str, Any]], used_names: set[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source_proxy in proxies:
        proxy = deepcopy(source_proxy)
        original = _proxy_name(proxy)
        if not original:
            raise SafeConfigError(
                "missing_proxy_name", "updated subscription contains an unnamed proxy"
            )

        candidate = original
        if candidate in used_names:
            base = f"Zod | {original}"
            candidate = base
            suffix = 2
            while candidate in used_names:
                candidate = f"{base} #{suffix}"
                suffix += 1
        proxy["name"] = candidate
        used_names.add(candidate)
        result.append(proxy)
    return result


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def merge_lazod(
    base: dict[str, Any],
    zod_proxies: list[dict[str, Any]],
    *,
    static_proxy_names: tuple[str, ...],
) -> tuple[dict[str, Any], int]:
    output = deepcopy(base)
    base_proxies = output.get("proxies")
    if not isinstance(base_proxies, list):
        raise SafeConfigError(
            "base_missing_proxies", "base configuration has no proxy list"
        )

    wanted_static = set(static_proxy_names)
    static_proxies = [
        deepcopy(proxy) for proxy in base_proxies if _proxy_name(proxy) in wanted_static
    ]
    found_static = {_proxy_name(proxy) for proxy in static_proxies}
    missing_static = wanted_static - found_static
    if missing_static:
        raise SafeConfigError(
            "missing_static_proxy",
            "base configuration is missing a declared static proxy",
        )

    used_names = set(found_static)
    normalized_zod = _deduplicate_zod_names(zod_proxies, used_names)
    zod_names = [_proxy_name(proxy) for proxy in normalized_zod]
    output["proxies"] = static_proxies + normalized_zod

    groups = output.get("proxy-groups")
    if not isinstance(groups, list):
        raise SafeConfigError(
            "base_missing_groups", "base configuration has no proxy groups"
        )

    original_pool_raw = output.get("proxy-list")
    original_pool = (
        original_pool_raw.get("proxies")
        if isinstance(original_pool_raw, dict)
        else None
    )
    original_pool = original_pool if isinstance(original_pool, list) else []

    full_pool_group_names = {"Proxy"}
    for group in groups:
        if (
            isinstance(group, dict)
            and isinstance(group.get("proxies"), list)
            and group.get("proxies") == original_pool
            and isinstance(group.get("name"), str)
        ):
            full_pool_group_names.add(group["name"])

    region_members: dict[str, list[str]] = {}
    for region, keywords in REGION_KEYWORDS.items():
        members = [
            name for name in zod_names if any(keyword in name for keyword in keywords)
        ]
        if region == "USA":
            members = list(static_proxy_names) + members
        region_members[region] = _unique(members)

    base_group_names = {
        group.get("name")
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }
    available_region_groups = {
        group_name
        for group_name, region in REGION_GROUP_TO_REGION.items()
        if group_name in base_group_names and region_members[region]
    }
    original_region_order = [
        name
        for name in original_pool
        if isinstance(name, str) and name in REGION_GROUP_TO_REGION
    ]
    region_order = original_region_order or list(DEFAULT_REGION_POOL_ORDER)
    selectable_regions = [
        name for name in region_order if name in available_region_groups
    ]
    full_pool = _unique(
        ["DIRECT", *selectable_regions, *static_proxy_names, *zod_names]
    )

    rebuilt_groups: list[dict[str, Any]] = []
    for source_group in groups:
        if not isinstance(source_group, dict):
            raise SafeConfigError(
                "invalid_group", "base configuration contains an invalid proxy group"
            )
        group = deepcopy(source_group)
        name = group.get("name")
        if not isinstance(name, str) or not name:
            raise SafeConfigError(
                "invalid_group_name",
                "base configuration contains an unnamed proxy group",
            )

        region = REGION_GROUP_TO_REGION.get(name)
        if region is not None:
            if name not in available_region_groups:
                continue
            group["proxies"] = region_members[region]
        elif name in full_pool_group_names:
            group["proxies"] = full_pool
        rebuilt_groups.append(group)

    output["proxy-groups"] = rebuilt_groups
    output.pop("proxy-list", None)
    validate_references(output)
    return output, len(normalized_zod)


def validate_references(config: dict[str, Any]) -> None:
    proxies = config.get("proxies", [])
    groups = config.get("proxy-groups", [])
    if not isinstance(proxies, list) or not isinstance(groups, list):
        raise SafeConfigError(
            "invalid_generated_config", "generated proxy or group list is invalid"
        )

    proxy_names = [_proxy_name(proxy) for proxy in proxies]
    if any(not name for name in proxy_names) or len(proxy_names) != len(
        set(proxy_names)
    ):
        raise SafeConfigError(
            "duplicate_proxy_name", "generated proxy names are empty or duplicated"
        )

    group_names = [group.get("name") for group in groups if isinstance(group, dict)]
    if len(group_names) != len(groups) or any(
        not isinstance(name, str) or not name for name in group_names
    ):
        raise SafeConfigError(
            "invalid_group_name", "generated proxy group names are invalid"
        )
    if len(group_names) != len(set(group_names)):
        raise SafeConfigError(
            "duplicate_group_name", "generated proxy group names are duplicated"
        )

    known = set(proxy_names) | set(group_names) | BUILTIN_OUTBOUNDS
    for group in groups:
        members = group.get("proxies")
        if members is None:
            continue
        if not isinstance(members, list) or not members:
            raise SafeConfigError(
                "empty_group", "generated configuration contains an empty proxy group"
            )
        if any(
            not isinstance(member, str) or member not in known for member in members
        ):
            raise SafeConfigError(
                "dangling_group_reference",
                "generated proxy group has a dangling reference",
            )

    rules = config.get("rules", [])
    if not isinstance(rules, list):
        raise SafeConfigError("invalid_rules", "generated rules list is invalid")
    for rule in rules:
        if not isinstance(rule, str):
            raise SafeConfigError(
                "invalid_rule", "generated configuration contains a non-string rule"
            )
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 2:
            raise SafeConfigError(
                "invalid_rule", "generated configuration contains a malformed rule"
            )
        target = parts[-2] if parts[-1].lower() == "no-resolve" else parts[-1]
        if target not in known:
            raise SafeConfigError(
                "dangling_rule_reference",
                "generated rule has a dangling policy reference",
            )
