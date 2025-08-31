from typing import List
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from config_loader import UIConfig


def escape_md_v2(text: str) -> str:
    """Escape MarkdownV2 special chars."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join("\\" + c if c in special else c for c in text)


def chunk(text: str, n: int) -> List[str]:
    """Split string into n-sized chunks."""
    return [text[i:i + n] for i in range(0, len(text), n)]


def build_keyboard(ui: UIConfig) -> InlineKeyboardMarkup:
    """Build inline keyboard from UIConfig."""
    rows: List[List[InlineKeyboardButton]] = []
    for row in ui.rows:
        btns: List[InlineKeyboardButton] = []
        for key in row:
            meta = ui.cmds.get(key)
            if not meta:
                continue
            btns.append(InlineKeyboardButton(meta.title, callback_data=f"cmd:{key}"))
        if btns:
            rows.append(btns)

    if not rows:
        rows = [[InlineKeyboardButton("✍️ Manual command", callback_data="cmd:custom")]]
    return InlineKeyboardMarkup(rows)
