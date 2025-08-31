import logging
from typing import Optional, List
from datetime import datetime
from pathlib import Path

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

    async def _send_local_file(self, target, path: Path, caption: str) -> None:
        """Send a local file in a version-agnostic way across PTB versions."""
        needs_close = False
        file_obj = None
        try:
            try:
                from telegram import FSInputFile as _FSInputFile  # type: ignore
                file_obj = _FSInputFile(str(path))
            except Exception:
                file_obj = open(path, "rb")
                needs_close = True
            await target.reply_document(document=file_obj, caption=caption)
        finally:
            if needs_close and file_obj is not None:
                try:
                    file_obj.close()
                except Exception:
                    pass

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

        # Archive directory and send as a file
        if getattr(meta, "archive", None):
            remote_dir = meta.archive or ""
            if not remote_dir:
                return await q.message.reply_text("Archive path not configured.")
            rc, data = await self.ssh.archive_dir(remote_dir)
            # Even if tar returned non-zero (warnings), still try to send if we have bytes
            if not data:
                return await self._ship_output(q.message, rc, "Archive failed or empty output.")

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            key_safe = key.replace("/", "-")
            filename = f"{key_safe}_{ts}.tar.gz"
            out_path = Path(self.settings.RUNTIME_DIR) / filename
            try:
                out_path.write_bytes(data)  # type: ignore[arg-type]
                await self._send_local_file(q.message, out_path, caption=f"📦 {filename}")
            finally:
                try:
                    if out_path.exists():
                        out_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return

        if meta.exec:
            # Capture remote epoch to disambiguate artifacts created by this run
            since_ts: int | None = None
            try:
                rc_t, out_t = await self.ssh.exec("date +%s")
                if rc_t == 0:
                    since_ts = int((out_t or "").strip())
            except Exception:
                since_ts = None

            rc, output = await self.ssh.exec(meta.exec)
            await self._ship_output(q.message, rc, output)

            # If an artifact glob is configured, fetch and send the newest matching file
            if getattr(meta, "artifact", None):
                rc2, data2, chosen = await self.ssh.download_artifact(meta.artifact or "", since_epoch=since_ts)
                if rc2 != 0:
                    err_txt = (
                        data2.decode(errors="replace") if isinstance(data2, (bytes, bytearray)) else str(data2)
                    )
                    await self._ship_output(q.message, rc2, err_txt)
                    return
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                base = (Path(chosen).name if chosen else f"artifact_{ts}.bin")
                key_safe = key.replace("/", "-")
                filename = f"{key_safe}_{base}"
                out_path = Path(self.settings.RUNTIME_DIR) / filename
                try:
                    out_path.write_bytes(data2)  # type: ignore[arg-type]
                    await self._send_local_file(q.message, out_path, caption=f"📦 {filename}")
                finally:
                    try:
                        if out_path.exists():
                            out_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            return

        return await q.message.reply_text("Command not configured.")

    async def manual_start(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        if not self._can(update.effective_user.id):
            return await update.message.reply_text("⛔️ Access denied.")
        await update.message.reply_text("✍️ Type a command (or /cancel):")
        return WAITING_MANUAL

    async def manual_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if not self._can(update.effective_user.id):
            await q.edit_message_text("⛔️ Access denied.")
            return ConversationHandler.END
        await q.message.reply_text("✍️ Type a command (or /cancel):")
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
        
        conv = ConversationHandler(
            entry_points=[
                CommandHandler("manual", self.manual_start),
                CallbackQueryHandler(self.manual_button, pattern=r"^cmd:custom$")
            ],
            states={WAITING_MANUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.manual_recv)]},
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        app.add_handler(conv)
        
        # Generic button handler after conversation so manual button is captured by conv
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_error_handler(self.errors)
        app.run_polling()


if __name__ == "__main__":
    BotApp().run()
