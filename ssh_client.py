import asyncio
import base64
import logging
import os
import re
import subprocess
import shlex
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

    async def archive_dir(self, remote_dir: str) -> Tuple[int, bytes]:
        """Create a tar.gz of remote_dir on the remote host and stream it back.

        Returns (exit_code, data_bytes_or_error_bytes)
        """
        c = self.cfg
        parent = os.path.dirname(remote_dir.rstrip("/")) or "."
        base = os.path.basename(remote_dir.rstrip("/")) or remote_dir
        # Use tar to write gzipped archive to stdout
        remote_cmd = (
            f"tar -C {shlex.quote(parent)} -czf - {shlex.quote(base)}"
        )

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

        log.info("SSH archive: %s", argv)
        try:
            proc = await asyncio.to_thread(
                subprocess.run, argv, capture_output=True, text=False, timeout=c.timeout
            )
            # Always return stdout (archive bytes), even if return code != 0
            # Many tar warnings still produce a usable archive on stdout.
            return proc.returncode, proc.stdout
        except subprocess.TimeoutExpired:
            return 124, b"Timeout"
        except Exception as e:
            return 1, f"Error: {e}".encode()

    async def download_artifact(self, glob_pattern: str, since_epoch: Optional[int] = None) -> Tuple[int, bytes, str | None]:
        """Find latest matching file by glob on remote and stream it back.

        Returns (exit_code, data_bytes_or_error_bytes, filename_or_none)
        """
        c = self.cfg
        # Use shell to expand glob, list by mtime, pick newest, then pipe file bytes
        if since_epoch is None:
            remote_cmd = (
                "set -e; "
                f"f=$(ls -1t -- {glob_pattern} 2>/dev/null | head -n1 || true); "
                "if [ -z \"$f\" ]; then echo 'No matching file' >&2; exit 2; fi; "
                "printf '%s\\n' \"$f\" 1>&2; "
                "cat -- \"$f\""
            )
        else:
            remote_cmd = (
                f"since={since_epoch}; "
                # Iterate files matching the glob; choose the newest with mtime >= since
                f"best=; best_ts=0; for f in {glob_pattern}; do [ -e \"$f\" ] || continue; "
                "ts=$(stat -c %Y \"$f\" 2>/dev/null || stat -f %m \"$f\" 2>/dev/null || echo 0); "
                "if [ \"$ts\" -ge \"$since\" ] && [ \"$ts\" -ge \"$best_ts\" ]; then best=\"$f\"; best_ts=\"$ts\"; fi; "
                "done; if [ -z \"$best\" ]; then echo 'No matching file' >&2; exit 2; fi; "
                "printf '%s\\n' \"$best\" 1>&2; cat -- \"$best\""
            )

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

        log.info("SSH fetch artifact: %s", argv)
        try:
            proc = await asyncio.to_thread(
                subprocess.run, argv, capture_output=True, text=False, timeout=c.timeout
            )
            if proc.returncode == 0:
                # Last line on stderr should be filename printed by remote_cmd
                chosen = (proc.stderr or b"").splitlines()[-1].decode(errors="replace") if proc.stderr else None
                return 0, proc.stdout, chosen
            return proc.returncode, (proc.stderr or proc.stdout), None
        except subprocess.TimeoutExpired:
            return 124, b"Timeout", None
        except Exception as e:
            return 1, f"Error: {e}".encode(), None
