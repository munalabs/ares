"""Tests for Ares KB client and build_brief with prev_surface."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ares_integration.kb import KBClient, target_id
from ares_integration.adapter import build_brief

from tests.conftest_ares import make_job_spec


ARGOS_URL = "https://argos.test"
TOKEN = "tok"
TENANT = "tenant-001"
TARGET_URL = "https://staging.acme.com"


def _make_kb() -> KBClient:
    return KBClient(argos_url=ARGOS_URL, token=TOKEN, tenant_id=TENANT)


def _mock_http(status: int, body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return resp, client


# ---------------------------------------------------------------------------
# target_id
# ---------------------------------------------------------------------------

def test_target_id_deterministic():
    assert target_id(TARGET_URL) == target_id(TARGET_URL)
    assert len(target_id(TARGET_URL)) == 32


def test_target_id_matches_argos():
    expected = hashlib.sha256(TARGET_URL.encode()).hexdigest()[:32]
    assert target_id(TARGET_URL) == expected


# ---------------------------------------------------------------------------
# fetch_surface
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_surface_found():
    surface = {"endpoints": ["/api/users"], "auth_type": "jwt"}
    resp, client = _mock_http(200, {"surface_data": surface})
    client.get = AsyncMock(return_value=resp)

    with patch("ares_integration.kb.httpx.AsyncClient", return_value=client):
        result = await _make_kb().fetch_surface(TARGET_URL)

    assert result == surface


@pytest.mark.asyncio
async def test_fetch_surface_not_found_returns_none():
    resp, client = _mock_http(404)
    client.get = AsyncMock(return_value=resp)

    with patch("ares_integration.kb.httpx.AsyncClient", return_value=client):
        result = await _make_kb().fetch_surface(TARGET_URL)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_surface_error_returns_none():
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=Exception("timeout"))

    with patch("ares_integration.kb.httpx.AsyncClient", return_value=client):
        result = await _make_kb().fetch_surface(TARGET_URL)

    assert result is None


# ---------------------------------------------------------------------------
# push_surface
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_surface_success():
    resp, client = _mock_http(201)
    client.post = AsyncMock(return_value=resp)

    with patch("ares_integration.kb.httpx.AsyncClient", return_value=client):
        ok = await _make_kb().push_surface(TARGET_URL, {"findings": []})

    assert ok is True
    posted = client.post.call_args.kwargs["json"]
    assert posted["target_url"] == TARGET_URL
    assert posted["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_push_surface_error_returns_false():
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("ares_integration.kb.httpx.AsyncClient", return_value=client):
        ok = await _make_kb().push_surface(TARGET_URL, {})

    assert ok is False


# ---------------------------------------------------------------------------
# build_brief with prev_surface
# ---------------------------------------------------------------------------

def test_build_brief_without_prev_surface():
    spec = make_job_spec()
    brief = build_brief(spec)
    assert "Focus on recently" not in brief or "known issues" not in brief.lower()
    assert "Engagement ID" in brief


def test_build_brief_with_prev_surface_findings():
    spec = make_job_spec()
    prev = {
        "findings": [
            {"id": "f1", "title": "SQL Injection in /api/users", "severity": "high"},
            {"id": "f2", "title": "XSS in search param", "severity": "medium"},
        ]
    }
    brief = build_brief(spec, prev_surface=prev)
    assert "2 issue" in brief
    assert "SQL Injection" in brief
    assert "Focus on new attack surface" in brief


def test_build_brief_with_prev_surface_no_findings():
    spec = make_job_spec()
    prev = {"findings": []}
    brief = build_brief(spec, prev_surface=prev)
    assert "no issues" in brief.lower()
    assert "Focus on new attack surface" in brief


def test_build_brief_prev_surface_does_not_break_brief_structure():
    spec = make_job_spec()
    prev = {"findings": [{"id": "x", "title": "IDOR", "severity": "high"}]}
    brief = build_brief(spec, prev_surface=prev)
    assert "Engagement ID" in brief
    assert "Budget remaining" in brief
    assert "Go." in brief
