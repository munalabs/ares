"""Shared test helpers for ares integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from muna_agentsdk import DynamicTarget, IdentityContext, JobSpec
from muna_agentsdk._version import SDK_VERSION
from muna_vaultsdk import VaultRef


def make_job_spec(tenant_id: str = "tenant-test") -> JobSpec:
    return JobSpec(
        job_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        analysis_type="dynamic",
        target=DynamicTarget(
            base_url="https://staging.acme.com",
            scope="staging.acme.com",
            auth_context=VaultRef(
                path=str(uuid.uuid4()),
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            ),
        ),
        requester=IdentityContext(id="lancer-001", type="lancer"),
        sdk_version=SDK_VERSION,
        budget_remaining_usd=50.0,
    )
