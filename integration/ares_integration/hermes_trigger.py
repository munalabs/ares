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


class SSHTrigger:
    """Trigger Hermes on a remote host via SSH.

    Used when the NATS consumer runs on one machine (muna1) but Hermes is
    installed on another (hermes-ai). The brief is written to a temp file on
    the local machine, copied via scp, then 'hermes run --no-interactive'
    is invoked remotely via ssh.

    Environment variables:
      ARES_SSH_HOST      — remote host (e.g. 192.168.2.3 or hermes-ai)
      ARES_SSH_USER      — remote user (default: same as local)
      ARES_SSH_KEY       — path to SSH private key (default: ~/.ssh/id_ed25519)
      ARES_SSH_REMOTE_DIR — remote output dir (default: ~/pentest-output)
    """

    def __init__(
        self,
        host: str,
        user: str | None = None,
        key_path: str | None = None,
        remote_dir: str = "~/pentest-output",
        hermes_bin: str = "hermes",
        profile: str | None = None,
    ) -> None:
        self._host = host
        self._user = user
        self._key = key_path or os.path.expanduser("~/.ssh/id_ed25519")
        self._remote_dir = remote_dir
        self._bin = hermes_bin
        self._profile = profile

    def _ssh(self, *args: str) -> list[str]:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-i", self._key]
        if self._user:
            cmd += [f"{self._user}@{self._host}"]
        else:
            cmd += [self._host]
        cmd += list(args)
        return cmd

    def start(self, engagement_id: str, brief: str) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix=f"{engagement_id}-brief-", delete=False
        ) as f:
            f.write(brief)
            local_brief = f.name

        remote_dir = self._remote_dir
        remote_brief = f"{remote_dir}/{engagement_id}-brief.txt"
        remote_log   = f"{remote_dir}/{engagement_id}-hermes.log"

        # Ensure remote dir exists
        subprocess.run(self._ssh("mkdir", "-p", remote_dir), check=False)

        # Copy brief
        scp_cmd = [
            "scp", "-o", "StrictHostKeyChecking=no", "-i", self._key,
            local_brief,
            f"{self._user + '@' if self._user else ''}{self._host}:{remote_brief}",
        ]
        subprocess.run(scp_cmd, check=True, timeout=15)

        # Launch hermes in background on remote host
        hermes_cmd = self._bin
        if self._profile:
            hermes_cmd += f" --profile {self._profile}"
        hermes_cmd += f" run --no-interactive @{remote_brief}"
        nohup_cmd = f"nohup {hermes_cmd} >> {remote_log} 2>&1 &"

        subprocess.run(self._ssh("bash", "-c", nohup_cmd), check=True, timeout=15)
        logger.info(f"Hermes triggered via SSH: host={self._host} engagement={engagement_id} log={remote_log}")

        os.unlink(local_brief)


def default_trigger() -> HermesTrigger:
    """Return the trigger configured by environment.

    ARES_TRIGGER=subprocess (default) — local hermes binary
    ARES_TRIGGER=ssh                  — hermes on remote host via SSH
    ARES_MOCK_TRIGGER=1               — test stub (no hermes invoked)
    """
    if os.getenv("ARES_MOCK_TRIGGER", "0") == "1":
        return MockTrigger()

    trigger_mode = os.getenv("ARES_TRIGGER", "subprocess")
    if trigger_mode == "ssh":
        host = os.environ["ARES_SSH_HOST"]  # required — fail fast if missing
        return SSHTrigger(
            host=host,
            user=os.getenv("ARES_SSH_USER"),
            key_path=os.getenv("ARES_SSH_KEY"),
            remote_dir=os.getenv("ARES_SSH_REMOTE_DIR", "~/pentest-output"),
            hermes_bin=os.getenv("HERMES_BIN", "hermes"),
            profile=os.getenv("HERMES_PROFILE"),
        )

    hermes_bin = os.getenv("HERMES_BIN", "hermes")
    profile = os.getenv("HERMES_PROFILE")
    return SubprocessTrigger(hermes_bin=hermes_bin, profile=profile)
