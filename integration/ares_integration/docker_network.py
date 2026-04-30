"""Per-engagement Docker network manager.

Creates an isolated bridge network for each engagement and removes it on cleanup.
Used when ARES_DOCKER_NETWORK_ISOLATION=true (requires Docker CLI on the host).
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_ISOLATION_ENABLED = os.getenv("ARES_DOCKER_NETWORK_ISOLATION", "").lower() in ("1", "true")


async def create_engagement_network(job_id: str) -> str | None:
    """Create a Docker network for an engagement. Returns the network name or None."""
    if not _ISOLATION_ENABLED:
        return None
    name = f"ares-net-{job_id[:12]}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "network", "create",
            "--driver", "bridge",
            # Note: do NOT use --internal — pentest tools need internet access to
            # reach target hosts. Isolation here is about separating engagements
            # from each other (different subnets), not from the internet.
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"Failed to create network {name}: {stderr.decode()[:100]}")
            return None
        logger.info(f"Created network {name} for engagement {job_id}")
        return name
    except FileNotFoundError:
        return None


async def remove_engagement_network(name: str) -> None:
    """Remove an engagement network. Best-effort."""
    if not name:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "network", "rm", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        logger.info(f"Removed network {name}")
    except Exception:
        pass
