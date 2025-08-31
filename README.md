# Telegram Admin Bot

A lightweight **Telegram bot in Docker** that lets you run commands on your server via **inline buttons** or **manual input**.
All commands are executed through **SSH** as your local user (e.g., `batyan`), so the bot itself does not need root privileges.

This project is designed for **self-hosted setups** (e.g. a Portainer-managed server) where you want to administer the host safely via Telegram.

---

## ✨ Features

* 🔑 **Secure SSH**: Commands run via `ssh` as a dedicated local user.
* 📱 **Inline keyboards**: Predefined buttons from `config.json`.
* ⌨️ **Manual commands**: Type any command in chat (`/manual`).
* 🔒 **User access control**: Only whitelisted Telegram user IDs can use the bot.
* ⚡ **Dockerized**: Runs fully inside a container, no Python setup required.
* 🔄 **Config reload**: Update `config.json` and run `/reload` to refresh without restart.

---

## 🛠 Requirements

* Docker / Docker Compose or Portainer
* Telegram bot token from [@BotFather](https://t.me/botfather)
* SSH access to the server as user `batyan` (or another user you configure)
* Public/private SSH key pair (private key stored in `.env` as Base64)

---

## ⚙️ Configuration

### 1. Create a Telegram bot

1. Talk to [@BotFather](https://t.me/botfather).
2. Run `/newbot` → get your **bot token**.
3. Copy the token into your `.env`.

### 2. Set environment variables

Copy `.env.example` → `.env` and adjust:

```dotenv
BOT_TOKEN=123456:ABC-DEF...
ALLOWED_USER_IDS=111111111,222222222

SSH_HOST=host.docker.internal   # or 127.0.0.1 with host network mode
SSH_PORT=22
SSH_USER=batyan
SSH_TIMEOUT_SEC=25

# private SSH key encoded in Base64
SSH_KEY_BASE64=LS0tLS1CRUdJTiB...
# optional, from ssh-keyscan
SSH_KNOWN_HOSTS_LINE=host.docker.internal ssh-ed25519 AAAAC...
```

> Generate Base64 from your private key:
>
> ```bash
> base64 -w0 ~/.ssh/id_ed25519 > id_ed25519.b64
> ```

### 3. Configure buttons

Edit `config.json`:

```json
{
  "ui": {
    "title": "Admin panel",
    "rows": [
      ["status", "disk"],
      ["logs", "restart"],
      ["custom"]
    ]
  },
  "commands": {
    "status":  { "title": "Status",    "exec": "uptime" },
    "disk":    { "title": "Disk",      "exec": "df -h" },
    "logs":    { "title": "Logs",      "exec": "journalctl -n 200 -u myservice" },
    "restart": { "title": "Restart",   "exec": "systemctl restart myservice" },
    "custom":  { "title": "✍️ Enter manually", "manual": true }
  }
}
```

---

## 🚀 Running

### With Docker Compose

```bash
docker compose up -d --build
```

### With Portainer

1. Go to **Stacks → Add stack**.
2. Paste `docker-compose.yml`.
3. Add `.env` values in the **Environment variables** section.
4. Deploy.

---

## 📖 Usage

* `/start` → show menu with buttons.
* Tap a button → executes the mapped command via SSH.
* `/manual` → enter any command manually.
* `/reload` → reload `config.json` without restarting the bot.

---

## 🔐 Security tips

* Use a **dedicated SSH key** for the bot.
* Restrict access: only your `ALLOWED_USER_IDS` can interact.
* Configure `sshd` to disallow password logins for safety.
* Keep the bot token and `.env` private (hidden in Portainer).

---

## 🧩 Example output

In Telegram:

```
✅ Success
```

```
 17:01:23 up 5 days,  2:44,  2 users,  load average: 0.11, 0.05, 0.01
```

---

## 📜 License

MIT — feel free to use and adapt.
