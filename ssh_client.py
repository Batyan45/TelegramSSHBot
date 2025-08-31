import asyncio
import base64
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from settings import Settings

log = logging.getLogger("tgbot.ssh")


@dataclass
class SSHConfig:
    host: str
    port: int
    user: str
    timeout: int
    key_path: Path
    known_hosts_path: Optional[Path]
    strict_mode: str  # "yes" | "accept-new" | "no"


class SSHClient:
    """Thin wrapper around system ssh client for simple remote exec."""

    def __init__(self, cfg: SSHConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def from_settings(s: Settings) -> "SSHClient":
        """Materialize key/known_hosts into runtime dir and build client."""
        runtime = s.RUNTIME_DIR
        runtime.mkdir(parents=True, exist_ok=True)

        # Private key from base64
        key_bytes = base64.b64decode(s.SSH_KEY_BASE64)
        key_path = runtime / "id_ed25519"
        key_path.write_bytes(key_bytes)
        key_path.chmod(0o600)

        # known_hosts (optional)
        strict = "accept-new"
        kh_path: Optional[Path] = None
        kh_line = (s.SSH_KNOWN_HOSTS_LINE or "").strip()
        if kh_line:
            strict = "yes"
            kh_path = runtime / "known_hosts"
            # Support multiple lines if provided
            lines = [ln for ln in re.split(r"[\r\n]+", kh_line) if ln.strip()]
            kh_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            kh_path.chmod(0o600)

        cfg = SSHConfig(
            host=s.SSH_HOST,
            port=s.SSH_PORT,
            user=s.SSH_USER,
            timeout=s.SSH_TIMEOUT_SEC,
            key_path=key_path,
            known_hosts_path=kh_path,
            strict_mode=strict,
        )
        return SSHClient(cfg)

    async def exec(self, remote_cmd: str) -> Tuple[int, str]:
        """Execute a remote command via ssh and return (exit_code, output)."""
        c = self.cfg
        argv = [
            "ssh",
            "-i", str(c.key_path),
            "-p", str(c.port),
            "-o", "BatchMode=yes",
            "-o", f"StrictHostKeyChecking={c.strict_mode}",
        ]
        if c.known_hosts_path:
            argv += ["-o", f"UserKnownHostsFile={c.known_hosts_path}"]
        argv += [f"{c.user}@{c.host}", "--", remote_cmd]

        log.info("SSH exec: %s", argv)
        try:
            proc = await asyncio.to_thread(
                subprocess.run, argv, capture_output=True, text=True, timeout=c.timeout
            )
            out = proc.stdout if proc.stdout else proc.stderr
            return proc.returncode, (out or "")
        except subprocess.TimeoutExpired:
            return 124, "Timeout"
        except Exception as e:
            return 1, f"Error: {e}"
