import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

log = logging.getLogger("tgbot.config")


@dataclass
class CommandMeta:
    """Button/canned command descriptor."""
    title: str
    exec: str | None = None
    manual: bool = False


@dataclass
class UIConfig:
    """Presentation config for menu and commands."""
    title: str
    rows: List[List[str]]
    cmds: Dict[str, CommandMeta]


def load_ui_config(path: str | Path) -> UIConfig:
    """Load config.json from disk. Fallback to manual-only if missing/broken."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw["ui"]["rows"]
        cmds = {
            k: CommandMeta(
                title=v.get("title", k),
                exec=v.get("exec"),
                manual=bool(v.get("manual", False)),
            )
            for k, v in raw["commands"].items()
        }
        title = raw["ui"].get("title", "Menu")
        return UIConfig(title=title, rows=rows, cmds=cmds)
    except Exception as e:
        log.warning("Failed to load %s (%s). Falling back to manual-only UI.", p, e)
        return UIConfig(
            title="Menu",
            rows=[["custom"]],
            cmds={"custom": CommandMeta(title="✍️ Manual command", manual=True)},
        )
