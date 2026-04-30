"""Argos Knowledge Base client for Ares — attack surface store.

Before each engagement: fetch previous surface so Hermes focuses on new endpoints.
After each engagement: push discovered surface so the next run starts warm.

Both operations are best-effort: a KB failure never causes an engagement to fail.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

__all__ = ["KBClient"]

logger = logging.getLogger(__name__)


def target_id(url: str) -> str:
    """Deterministic target identifier: sha256(url)[:32]. Must match Argos convention."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


class KBClient:
    def __init__(self, *, argos_url: str, token: str, tenant_id: str) -> None:
        self._base = argos_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._tenant_id = tenant_id

    async def fetch_surface(self, target_url: str) -> dict[str, Any] | None:
        """Return previous surface data or None if not found."""
        tid = target_id(target_url)
        url = f"{self._base}/v1/api/knowledge/targets/{tid}/surface"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers=self._headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json().get("surface_data")
        except Exception as e:
            logger.warning(f"KB fetch_surface failed (target={target_url}): {e}")
            return None

    async def push_surface(self, target_url: str, surface_data: dict[str, Any]) -> bool:
        """Upsert surface data for a target. Returns True on success."""
        tid = target_id(target_url)
        url = f"{self._base}/v1/api/knowledge/targets/{tid}/surface"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    url,
                    headers=self._headers,
                    json={
                        "tenant_id": self._tenant_id,
                        "target_url": target_url,
                        "surface_data": surface_data,
                    },
                )
            r.raise_for_status()
            logger.info(f"KB surface pushed: target={target_url} findings={len(surface_data.get('findings', []))}")
            return True
        except Exception as e:
            logger.warning(f"KB push_surface failed (target={target_url}): {e}")
            return False
