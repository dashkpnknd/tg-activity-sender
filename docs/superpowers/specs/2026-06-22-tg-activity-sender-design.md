# TG Activity Sender Design

## Goal

Build a reusable Telegram-administered campaign bot for recurring outreach from connected Telegram user accounts. The bot must let admins add accounts by QR login, manage message sequences, blacklists, campaign rules, schedules, and run campaigns against active or inactive dialogs without editing server files.

## Product Scope

The first version is a Telegram interface, not a web panel. Admins manage:

- Telegram user accounts connected by QR code.
- Message sequences made of ordered steps. Each step can contain text, media, voice, video notes, stickers, documents, or albums captured from a message sent to the admin bot.
- Campaigns with separate rules for active dialogs and inactive dialogs.
- Sending targets: chats, private dialogs, or both.
- Day threshold for activity matching.
- Allowed sending hours in Moscow time.
- Per-account delay between sends.
- Global blacklist of users/chats and campaign-specific exclusions.
- Start, pause, resume, and stop controls.
- Run logs and counters.

## Safety And Operating Constraints

- The bot is admin-only.
- Secrets, account sessions, databases, media cache, and logs are never committed to GitHub.
- Outbound sending uses conservative delays and respects FloodWait-style errors.
- Blacklist and stop-word handling are first-class features.
- The implementation is intended for existing/owned audiences and must not include bypasses for platform restrictions.

## Architecture

The application is a single Python service with three boundaries:

- `admin_bot`: aiogram handlers and keyboards for the Telegram control interface.
- `core`: database models, repositories, campaign selection rules, message serialization, and scheduler logic.
- `telegram`: Telethon account sessions, QR login, dialog scanning, and message delivery.

SQLite is used for the first release because the server is small and the workload is modest. The schema is versioned by SQLAlchemy metadata. The worker loop polls runnable campaigns, assigns work across enabled accounts, checks schedule windows, sends campaign sequences, and records delivery attempts.

## Data Model

- `Account`: Telegram user account metadata and session path.
- `MessageSequence`: reusable named sequence.
- `SequenceStep`: ordered message payload and delay after send.
- `Campaign`: rules, target options, schedule, status, counters.
- `BlacklistEntry`: global and optional campaign-level exclusions.
- `DeliveryLog`: result per recipient/chat and campaign.
- `AdminState`: lightweight state for multi-step Telegram UI flows.

## Telegram UI

Main menu:

- Accounts
- Sequences
- Campaigns
- Blacklist
- Logs
- Settings

Account flow:

1. Admin presses "Add account by QR".
2. Bot creates a Telethon QR login token.
3. Bot sends QR image and login URL.
4. Admin scans QR in Telegram.
5. Bot stores the account session and enables it.

Sequence flow:

1. Admin creates or opens a sequence.
2. Admin adds steps by sending messages to the bot.
3. Bot stores text/media payloads.
4. Admin can reorder, delete, preview, or set delay after each step.

Campaign flow:

1. Admin creates campaign.
2. Admin selects sequence.
3. Admin sets activity mode: active within N days, inactive for more than N days, or both as separate campaign variants.
4. Admin selects destinations: chats, private dialogs, or both.
5. Admin sets allowed hours and delay.
6. Admin starts, pauses, resumes, or stops campaign.

## Testing Strategy

Unit tests cover pure logic without Telegram network calls:

- Schedule window parsing and matching.
- Activity-mode recipient selection.
- Blacklist matching by ID, username, and chat link.
- Campaign state transitions.
- Message sequence ordering and delay totals.

Integration points to Telegram are wrapped behind interfaces so they can be tested with fakes.

