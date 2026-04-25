"""Hermes trigger interface.

Abstracts how Ares (Hermes agent) is invoked so the specific mechanism
can be swapped without changing the consumer or HTTP endpoint.

Current implementation: subprocess.
Future: Hermes webhook endpoint (once that API is available).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class HermesTrigger(Protocol):
    """How a pentest engagement is started."""

    def start(self, engagement_id: str, brief: str) -> None:
        """Kick off the engagement. Non-blocking — returns immediately."""
        ...


class SubprocessTrigger:
    """Trigger Hermes via 'hermes run' subprocess.

    The brief is written to a temp file so it doesn't get mangled by shell quoting.
    Hermes runs in the background (nohup) so this returns immediately.
    """

    def __init__(
        self,
        hermes_bin: str = "hermes",
        profile: str | None = None,
        output_dir: str | None = None,
    ) -> None:
        self._bin = hermes_bin
        self._profile = profile
        self._output_dir = Path(output_dir) if output_dir else Path(os.path.expanduser("~/pentest-output"))

    def start(self, engagement_id: str, brief: str) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        brief_file = self._output_dir / f"{engagement_id}-brief.txt"
        brief_file.write_text(brief)

        cmd = [self._bin]
        if self._profile:
            cmd += ["--profile", self._profile]
        cmd += ["run", "--no-interactive", f"@{brief_file}"]

        log_file = self._output_dir / f"{engagement_id}-hermes.log"

        with open(log_file, "w") as log:
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=log,
                start_new_session=True,  # Detach from parent process
            )
        logger.info(f"Hermes triggered: engagement={engagement_id} log={log_file}")


class MockTrigger:
    """Test trigger — writes a sentinel file instead of invoking Hermes.

    Used in unit tests and when ARES_MOCK_TRIGGER=1.
    """

    def __init__(self, output_dir: str | None = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else Path(os.path.expanduser("~/pentest-output"))

    def start(self, engagement_id: str, brief: str) -> None:
        out = self._output_dir / engagement_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "brief.txt").write_text(brief)
        (out / "engagement-metadata.json").write_text(
            f'{{"engagement_id": "{engagement_id}", "mock": true}}'
        )
        logger.info(f"MockTrigger: engagement dir created at {out}")


def default_trigger() -> HermesTrigger:
    """Return the trigger configured by environment."""
    if os.getenv("ARES_MOCK_TRIGGER", "0") == "1":
        return MockTrigger()
    hermes_bin = os.getenv("HERMES_BIN", "hermes")
    profile = os.getenv("HERMES_PROFILE")
    return SubprocessTrigger(hermes_bin=hermes_bin, profile=profile)
