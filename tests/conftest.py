from __future__ import annotations

from copy import deepcopy

import pytest


@pytest.fixture
def base_config() -> dict:
    full_pool = [
        "DIRECT",
        "USA",
        "TWN",
        "TWN Auto",
        "SGP",
        "SGP Auto",
        "HKG",
        "HKG Auto",
        "JPN",
        "JPN Auto",
        "LA",
        "OLD-ZOD",
    ]
    groups = [
        {"name": "Proxy", "type": "select", "proxies": deepcopy(full_pool)},
        {"name": "USA", "type": "select", "proxies": ["LA"]},
    ]
    for region in ("HKG", "JPN", "SGP", "TWN"):
        groups.append({"name": region, "type": "select", "proxies": ["OLD-ZOD"]})
        groups.append(
            {
                "name": f"{region} Auto",
                "type": "url-test",
                "proxies": ["OLD-ZOD"],
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
            }
        )
    groups.extend(
        [
            {"name": "AI", "type": "select", "proxies": deepcopy(full_pool)},
            {"name": "Final", "type": "select", "proxies": deepcopy(full_pool)},
        ]
    )
    return {
        "mode": "rule",
        "proxies": [
            {
                "name": "LA",
                "type": "ss",
                "server": "192.0.2.10",
                "port": 443,
                "cipher": "aes-128-gcm",
                "password": "static-secret",
            },
            {
                "name": "OLD-ZOD",
                "type": "ss",
                "server": "192.0.2.11",
                "port": 443,
                "cipher": "aes-128-gcm",
                "password": "old-secret",
            },
        ],
        "proxy-list": {"proxies": full_pool},
        "proxy-groups": groups,
        "rules": ["DOMAIN-SUFFIX,example.com,AI", "MATCH,Final"],
    }


@pytest.fixture
def bootstrap_config() -> dict:
    return {
        "proxies": [
            {
                "name": "PRO | 订阅专用节点AN | 0倍",
                "type": "anytls",
                "server": "198.51.100.1",
                "port": 443,
                "password": "bootstrap-secret",
            },
            {
                "name": "PRO | 订阅专用节点VL | 0倍",
                "type": "vless",
                "server": "198.51.100.2",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000001",
            },
            *[
                {
                    "name": name,
                    "type": "ss",
                    "server": "198.51.100.3",
                    "port": 443,
                    "cipher": "aes-128-gcm",
                    "password": "v0-secret",
                }
                for name in (
                    "V0-香港节点01 | 1倍",
                    "V0-香港节点02 | 1倍",
                    "V0-新加坡节点01 | 1倍",
                    "V0-新加坡节点02 | 1倍",
                )
            ],
        ]
    }


@pytest.fixture
def updated_config() -> dict:
    names = (
        "V0-香港节点01 | 1倍",
        "PRO | 美国洛杉矶 VL1 | 1倍",
        "PRO | 香港BGP VL01 | 1倍",
        "PRO | 台湾BGP VL01 | 1倍",
        "PRO | 日本BGP VL01 | 1倍",
        "PRO | 新加坡MIS VL3 | 1倍",
    )
    proxies = [
        {
            "name": name,
            "type": "ss",
            "server": f"203.0.113.{index}",
            "port": 443,
            "cipher": "aes-128-gcm",
            "password": "node-secret",
        }
        for index, name in enumerate(names, start=1)
    ]
    proxies.extend(
        [
            {
                "name": "节点变动日期20260817",
                "type": "ss",
                "server": "203.0.113.100",
                "port": 443,
                "cipher": "aes-128-gcm",
                "password": "info",
            },
            {
                "name": "PRO | 订阅专用节点AN | 0倍",
                "type": "anytls",
                "server": "203.0.113.101",
                "port": 443,
                "password": "update",
            },
        ]
    )
    return {"proxies": proxies}
