import os
from dataclasses import dataclass
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

# Load .env only if OS env var not already set. This way:
# - local runs: values come from .env (nice DX);
# - containers/Portainer: values from environment take precedence.
load_dotenv(override=False)


def _parse_int(s: str | None, default: int) -> int:
    try:
        return int(s) if s is not None else default
    except ValueError:
        return default


def _parse_users(s: str | None) -> Set[int]:
    if not s:
        return set()
    out = set()
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            pass
    return out


def _choose_runtime_dir() -> Path:
    """
    Choose a writable runtime directory for local & container runs.
    Priority: $RUNTIME_DIR -> ./runtime -> ~/.tgbot/runtime
    """
    env_val = os.getenv("RUNTIME_DIR")
    if env_val:
        p = Path(env_val).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    local = Path("./runtime").resolve()
    try:
        local.mkdir(parents=True, exist_ok=True)
        return local
    except Exception:
        pass

    home = Path("~/.tgbot/runtime").expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


@dataclass(frozen=True)
class Settings:
    # Telegram
    BOT_TOKEN: str
    ALLOWED_USER_IDS: Set[int]

    # SSH
    SSH_HOST: str
    SSH_PORT: int
    SSH_USER: str
    SSH_TIMEOUT_SEC: int
    SSH_KEY_BASE64: str
    SSH_KNOWN_HOSTS_LINE: str | None

    # Files
    CONFIG_PATH: Path
    RUNTIME_DIR: Path


def get_settings() -> Settings:
    """Build settings from env (or .env for local runs)."""
    # Telegram
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required.")
    allowed = _parse_users(os.getenv("ALLOWED_USER_IDS"))

    # SSH
    ssh_host = os.getenv("SSH_HOST", "host.docker.internal")
    ssh_port = _parse_int(os.getenv("SSH_PORT"), 22)
    ssh_user = os.getenv("SSH_USER", "batyan")  # default as requested
    ssh_timeout = _parse_int(os.getenv("SSH_TIMEOUT_SEC"), 25)
    ssh_key_b64 = os.getenv("SSH_KEY_BASE64")
    if not ssh_key_b64:
        raise RuntimeError("SSH_KEY_BASE64 is required (base64 of your private key).")
    ssh_kh_line = os.getenv("SSH_KNOWN_HOSTS_LINE")  # optional

    # Files
    cfg_path = Path(os.getenv("CONFIG_PATH", "./config.json")).expanduser().resolve()
    runtime_dir = _choose_runtime_dir()

    return Settings(
        BOT_TOKEN=bot_token,
        ALLOWED_USER_IDS=allowed,
        SSH_HOST=ssh_host,
        SSH_PORT=ssh_port,
        SSH_USER=ssh_user,
        SSH_TIMEOUT_SEC=ssh_timeout,
        SSH_KEY_BASE64=ssh_key_b64,
        SSH_KNOWN_HOSTS_LINE=ssh_kh_line,
        CONFIG_PATH=cfg_path,
        RUNTIME_DIR=runtime_dir,
    )
