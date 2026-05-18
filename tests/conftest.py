"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cfg() -> dict:
    return {
        "microware": {
            "base_url": "https://api.microware.hu",
            "username": "testuser",
            "owner_contact_id": "12345",
            "ns1": "ns1.microware.hu",
            "ns2": "ns2.microware.hu",
            "registration_years": 2,
            "domain_type": "1f",
        },
        "backorder": {
            "enabled": True,
            "dry_run": False,
            "daily_cap": 10,
            "tunnel_url": "https://tunnel.example/backorder",
            "action_ttl_hours": 24,
        },
    }


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path
