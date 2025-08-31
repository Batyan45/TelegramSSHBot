import logging
from typing import Optional, List

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

from settings import get_settings
from config_loader import UIConfig, load_ui_config, CommandMeta
from ssh_client import SSHClient
from ui import build_keyboard, escape_md_v2, chunk

# Telegram conversation step for manual input
WAITING_MANUAL = 1

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tgbot")


class BotApp:
    """Telegram bot app wired to an SSH executor."""

    def __init__(self) -> None:
        # Load env (from OS env or .env) and materialize runtime files
        self.settings = get_settings()
        self.token = self.settings.BOT_TOKEN
        self.allowed_users = self.settings.ALLOWED_USER_IDS

        # UI config (buttons/canned commands)
        self.cfg_path = str(self.settings.CONFIG_PATH)
        self.ui: UIConfig = load_ui_config(self.cfg_path)

        # SSH client bound to runtime dir (key + known_hosts)
        self.ssh = SSHClient.from_settings(self.settings)

        # Safe message chunk size for Telegram MarkdownV2
        self.max_out = 3800

    # ---------- helpers
    def _kb(self) -> InlineKeyboardMarkup:
        return build_keyboard(self.ui)

    def _can(self, uid: int) -> bool:
        return uid in self.allowed_users

    async def _send_menu(self, update: Update, text: Optional[str] = None) -> None:
        msg = self.ui.title if not text else f"{self.ui.title}\n\n{text}"
        if update.message:
            await update.message.reply_text(msg, reply_markup=self._kb())
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=self._kb())

    async def _ship_output(self, target, rc: int, out: str) -> None:
        head = "✅ Success" if rc == 0 else f"❗️ Exit code {rc}"
        text = out.strip() or "(empty)"
        for i, part in enumerate(chunk(text, self.max_out)):
            body = f"{head if i == 0 else '…'}\n```\n{escape_md_v2(part)}\n```"
            await target.reply_text(body, parse_mode="MarkdownV2")

    # ---------- handlers
    async def start(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        await self._send_menu(update)

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        await self._send_menu(update, "Press a button or use /manual to type a command. /reload reloads config.json.")

    async def reload(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        try:
            self.ui = load_ui_config(self.cfg_path)
            await update.message.reply_text("♻️ Config reloaded.")
            await self._send_menu(update)
        except Exception as e:
            await update.message.reply_text(f"Reload failed: {e}")

    async def on_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        if not self._can(update.effective_user.id):
            return await q.edit_message_text("⛔️ Access denied.")

        data = (q.data or "")
        if not data.startswith("cmd:"):
            return
        key = data.split(":", 1)[1]
        meta: Optional[CommandMeta] = self.ui.cmds.get(key)

        if not meta:
            return await q.message.reply_text("Unknown command.")

        if meta.manual:
            return await q.message.reply_text("✍️ Type a command (or /cancel):")

        if not meta.exec:
            return await q.message.reply_text("Command not configured.")

        rc, output = await self.ssh.exec(meta.exec)
        await self._ship_output(q.message, rc, output)

    async def manual_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        await update.message.reply_text("✍️ Type a command (or /cancel):")
        return WAITING_MANUAL

    async def manual_recv(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        cmd = (update.message.text or "").strip()
        if not cmd:
            return await update.message.reply_text("Empty command.")
        rc, output = await self.ssh.exec(cmd)
        await self._ship_output(update.message, rc, output)
        return ConversationHandler.END

    async def cancel(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Cancelled.")
        return ConversationHandler.END

    async def errors(self, update: object, ctx: ContextTypes.DEFAULT_TYPE):
        log.exception("Unhandled error: %s", ctx.error)

    def run(self) -> None:
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help))
        app.add_handler(CommandHandler("reload", self.reload))
        app.add_handler(CallbackQueryHandler(self.on_button))

        conv = ConversationHandler(
            entry_points=[CommandHandler("manual", self.manual_start)],
            states={WAITING_MANUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.manual_recv)]},
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        app.add_handler(conv)
        app.add_error_handler(self.errors)
        app.run_polling()


if __name__ == "__main__":
    BotApp().run()
