# Private Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create private one-time reminders in Gwen, deliver them once through Telegram, and mirror future alerts as local iPhone notifications.

**Architecture:** The Windows Gwen database is authoritative. A deterministic parser accepts only explicit reminder phrases with an unambiguous Guatemala timestamp; the Telegram bot runs a minute scheduler and atomically claims due rows before sending. The native app reads upcoming reminders over the existing authenticated Tailscale connection and schedules only those local notifications.

**Tech Stack:** Python/FastAPI/SQLAlchemy/SQLite, python-telegram-bot, SwiftUI, UserNotifications, pytest, Swift Testing.

---

### Task 1: Reminder domain and parser

**Files:** `src/gwen/models.py`, `src/gwen/reminders.py`, `src/gwen/repository.py`, `tests/test_reminders.py`.

- [ ] Write failing tests for an explicit one-time reminder, ambiguous text rejection, Guatemala timezone conversion, and an already-delivered row never being claimed twice.
- [ ] Add `Reminder` with user id, text, due UTC time, timezone, status and delivered timestamp; add repository create/list/cancel/atomic due-claim methods.
- [ ] Implement deterministic Spanish reminder parsing for exact date/time input only; no provider call and no recurrence.
- [ ] Run `pytest tests/test_reminders.py -q` and verify green.

### Task 2: Telegram delivery and server APIs

**Files:** `src/gwen/bot.py`, `src/gwen/web.py`, `tests/test_bot.py`, `tests/test_web.py`.

- [ ] Write failing tests for a once-per-minute bot job that sends only a successfully claimed due reminder and for authenticated list/cancel API routes.
- [ ] Register the scheduler on bot startup; record delivered before any retry can duplicate notification and log only error types.
- [ ] Add `GET /api/reminders` and `DELETE /api/reminders/{id}` without returning message history, tokens or secrets.
- [ ] Run focused backend tests.

### Task 3: iPhone synchronization

**Files:** `gwen-ios/Models/ChatModels.swift`, `gwen-ios/Networking/GwenClient.swift`, `gwen-ios/Models/ChatController.swift`, `gwen-ios/Services/LocalNotifications.swift`, `gwen-ios/UI/SettingsView.swift`, `gwen-iosTests/ContractTests.swift`.

- [ ] Write failing Swift tests for reminder decoding and authenticated list/cancel requests.
- [ ] Add reminder state and refresh/cancel methods; refresh after connection and when Settings opens.
- [ ] Schedule local alerts using the server due time; replace stale pending Gwen alerts, retain at most 64 future alerts, and never create notifications for cancelled/delivered rows.
- [ ] Add Settings list/cancel UI and run unsigned Xcode build plus Swift tests.

### Task 4: Verification

- [ ] Run Ruff, backend test suite, Swift tests, iOS target builds and `git diff --check`.
- [ ] Do not commit or push without explicit user authorization.
