# TG Activity Sender

Universal Telegram-administered campaign sender for reusable activity-based outreach.

## Features

- Add Telegram user accounts by QR login.
- Manage message sequences from the Telegram bot interface.
- Store text and media steps, including video notes.
- Create reusable campaigns by dialog activity threshold.
- Send to chats, private dialogs, or both.
- Restrict sending to configured hours.
- Manage blacklist from Telegram.
- View delivery logs.

## Stack

- Python 3.11+
- aiogram 3 for the admin bot
- Telethon for Telegram user accounts and QR login
- SQLAlchemy + SQLite

## Local Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` on the server. Do not commit it.

```bash
.venv/bin/python -m tg_activity_sender.app
```

## Admin Access

`ADMIN_IDS` controls who can open the admin interface. If it is empty, any user who has the bot token link can open the interface. For production, set explicit Telegram IDs.

## Server Data

Runtime data is intentionally ignored by Git:

- `data/`
- `sessions/`
- `logs/`
- `.env`
- `*.session`
- SQLite databases

## First Run Flow

1. Start the service.
2. Open the admin bot and press `Аккаунты`.
3. Add one or more accounts by QR.
4. Create a message sequence.
5. Create a campaign.
6. Open campaign list and press the run button.

## Notes

This tool is designed for existing/owned audiences. Keep conservative delays, maintain the blacklist, and do not use it to bypass Telegram limits.

