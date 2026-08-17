from __future__ import annotations

from pathlib import Path

import pytest

from app.merge import (
    extract_update_candidates,
    filter_zod_proxies,
    merge_lazod,
    validate_references,
)
from app.yaml_utils import SafeConfigError, load_yaml_bytes


def test_extracts_anytls_then_vless(bootstrap_config: dict) -> None:
    candidates = extract_update_candidates(bootstrap_config)
    assert [candidate["type"] for candidate in candidates] == ["anytls", "vless"]


def test_rejects_forbidden_control_character() -> None:
    with pytest.raises(SafeConfigError, match="control") as error:
        load_yaml_bytes(b"proxies:\n  - name: bad\xc2\x8dname\n", source="test")
    assert error.value.code == "forbidden_control"


def test_filters_information_and_update_nodes(updated_config: dict) -> None:
    proxies = filter_zod_proxies(updated_config, minimum=5)
    names = {proxy["name"] for proxy in proxies}
    assert "节点变动日期20260817" not in names
    assert "PRO | 订阅专用节点AN | 0倍" not in names
    assert "V0-香港节点01 | 1倍" in names


def test_rejects_bootstrap_only(bootstrap_config: dict) -> None:
    with pytest.raises(SafeConfigError) as error:
        filter_zod_proxies(bootstrap_config, minimum=4)
    assert error.value.code == "bootstrap_only"


def test_merge_rebuilds_groups_and_keeps_static_node(
    base_config: dict, updated_config: dict
) -> None:
    zod = filter_zod_proxies(updated_config, minimum=5)
    merged, count = merge_lazod(base_config, zod, static_proxy_names=("LA",))

    assert count == 6
    assert "proxy-list" not in merged
    assert merged["proxies"][0]["name"] == "LA"
    assert "OLD-ZOD" not in {proxy["name"] for proxy in merged["proxies"]}

    groups = {group["name"]: group for group in merged["proxy-groups"]}
    assert groups["USA"]["proxies"] == ["LA", "PRO | 美国洛杉矶 VL1 | 1倍"]
    assert groups["HKG"]["proxies"] == [
        "V0-香港节点01 | 1倍",
        "PRO | 香港BGP VL01 | 1倍",
    ]
    assert "PRO | 新加坡MIS VL3 | 1倍" in groups["SGP Auto"]["proxies"]
    assert groups["Proxy"]["proxies"] == groups["AI"]["proxies"]
    validate_references(merged)


def test_merge_omits_empty_region_groups(base_config: dict) -> None:
    only_usa = [
        {
            "name": "美国测试节点",
            "type": "ss",
            "server": "203.0.113.1",
            "port": 443,
            "cipher": "aes-128-gcm",
            "password": "test",
        }
    ]
    merged, _ = merge_lazod(base_config, only_usa, static_proxy_names=("LA",))
    groups = {group["name"]: group for group in merged["proxy-groups"]}
    assert set(groups).isdisjoint(
        {"HKG", "HKG Auto", "JPN", "JPN Auto", "SGP", "SGP Auto", "TWN", "TWN Auto"}
    )
    assert all("HKG" not in group.get("proxies", []) for group in groups.values())


def test_merge_disambiguates_static_name_collision(base_config: dict) -> None:
    collision = [
        {
            "name": "LA",
            "type": "ss",
            "server": "203.0.113.1",
            "port": 443,
            "cipher": "aes-128-gcm",
            "password": "test",
        },
        {
            "name": "LA",
            "type": "ss",
            "server": "203.0.113.2",
            "port": 443,
            "cipher": "aes-128-gcm",
            "password": "test",
        },
    ]
    merged, _ = merge_lazod(base_config, collision, static_proxy_names=("LA",))
    assert [proxy["name"] for proxy in merged["proxies"]] == [
        "LA",
        "Zod | LA",
        "Zod | LA #2",
    ]


def test_real_lazod_template_merges_without_dangling_references(
    updated_config: dict,
) -> None:
    path = Path(__file__).parents[1] / "LAZod.yaml"
    if not path.exists():
        pytest.skip("private LAZod template is not present")
    base = load_yaml_bytes(path.read_bytes(), source="private test template")
    zod = filter_zod_proxies(updated_config, minimum=5)
    merged, _ = merge_lazod(base, zod, static_proxy_names=("LA",))
    validate_references(merged)
