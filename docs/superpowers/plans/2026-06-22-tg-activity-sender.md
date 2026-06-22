# TG Activity Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Telegram-administered campaign bot with QR account login, message sequences, blacklists, schedules, and activity-based outreach rules.

**Architecture:** A Python service split into pure core logic, database/repository code, Telegram admin handlers, and Telegram account delivery adapters. Unit tests drive the core rules first; Telegram network features are behind small service interfaces.

**Tech Stack:** Python 3.11+, aiogram 3, Telethon, SQLAlchemy 2, SQLite, qrcode, python-dotenv, pytest.

## Global Constraints

- Admin-only access.
- Do not commit `.env`, tokens, Telegram sessions, databases, media cache, or logs.
- QR login is the primary account onboarding path.
- Campaigns must be reusable for different accounts, texts, schedules, and audiences.
- Message sequences must support multiple steps and video notes.
- Blacklist and stop-word behavior must be built into the workflow.
- Sending must use conservative delays and must not include restriction-bypass logic.

---

### Task 1: Project Skeleton And Pure Core Tests

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `tg_activity_sender/__init__.py`
- Create: `tg_activity_sender/core.py`
- Create: `tests/test_core.py`

**Interfaces:**
- Produces: `ScheduleWindow`, `ActivityMode`, `CampaignStatus`, `Recipient`, `Blacklist`, `select_recipients`, and `can_transition`.

- [ ] Write failing tests for schedule windows, blacklist matching, activity selection, and campaign transitions.
- [ ] Run `pytest tests/test_core.py -v` and confirm failure before implementation.
- [ ] Implement minimal pure core logic.
- [ ] Run `pytest tests/test_core.py -v` and confirm pass.

### Task 2: Database Models And Repositories

**Files:**
- Create: `tg_activity_sender/config.py`
- Create: `tg_activity_sender/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: core enums and data rules from Task 1.
- Produces: `init_db`, SQLAlchemy models, and `Database` helper.

- [ ] Write failing tests for creating accounts, sequences, campaigns, blacklist entries, and logs.
- [ ] Run `pytest tests/test_db.py -v` and confirm failure before implementation.
- [ ] Implement SQLAlchemy models and helper methods.
- [ ] Run database tests and confirm pass.

### Task 3: Message Payloads And Media Cache

**Files:**
- Create: `tg_activity_sender/messages.py`
- Create: `tests/test_messages.py`

**Interfaces:**
- Produces: `StoredMessage`, `MessageStep`, `sequence_duration_seconds`, and payload validation.

- [ ] Write failing tests for ordered steps, video note payload metadata, and delay totals.
- [ ] Run `pytest tests/test_messages.py -v` and confirm failure before implementation.
- [ ] Implement message payload dataclasses and validation.
- [ ] Run message tests and confirm pass.

### Task 4: Telegram Account QR And Delivery Services

**Files:**
- Create: `tg_activity_sender/telegram_accounts.py`
- Create: `tests/test_telegram_accounts.py`

**Interfaces:**
- Consumes: DB account rows and message payloads.
- Produces: `AccountManager`, `QrLoginTicket`, and delivery interface methods.

- [ ] Write failing tests using fakes for QR ticket lifecycle and account enabling.
- [ ] Run targeted tests and confirm failure before implementation.
- [ ] Implement Telethon-facing wrappers with network code isolated.
- [ ] Run targeted tests and confirm pass.

### Task 5: Admin Bot Interface

**Files:**
- Create: `tg_activity_sender/admin_bot.py`
- Create: `tg_activity_sender/keyboards.py`
- Create: `tg_activity_sender/states.py`
- Create: `tests/test_keyboards.py`

**Interfaces:**
- Consumes: database helper, core rules, QR/account services.
- Produces: aiogram router, main menu, account, sequence, campaign, blacklist, and log handlers.

- [ ] Write failing tests for keyboard structures and callback IDs.
- [ ] Run targeted tests and confirm failure before implementation.
- [ ] Implement keyboards and handlers.
- [ ] Run targeted tests and confirm pass.

### Task 6: Campaign Worker

**Files:**
- Create: `tg_activity_sender/worker.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: database helper, core selection rules, account delivery service.
- Produces: campaign polling and send orchestration.

- [ ] Write failing tests for schedule gating, recipient filtering, and delivery logging with fake delivery.
- [ ] Run targeted tests and confirm failure before implementation.
- [ ] Implement worker orchestration.
- [ ] Run targeted tests and confirm pass.

### Task 7: Application Entrypoint And Deployment Docs

**Files:**
- Create: `tg_activity_sender/app.py`
- Create: `README.md`
- Create: `deploy/tg-activity-sender.service`
- Create: `deploy/install.sh`

**Interfaces:**
- Consumes: config, DB, admin bot, worker.
- Produces: runnable service entrypoint and server instructions.

- [ ] Add entrypoint and deployment files.
- [ ] Run full test suite.
- [ ] Commit and push to GitHub.
