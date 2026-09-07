# Natural Reminder Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Gwen-generated, concise confirmation and delivery wording to private reminders while preserving deterministic creation and delivery.

**Architecture:** The assistant asks the configured provider for a constrained JSON pair only after it has parsed an unambiguous reminder. The database stores the delivery wording with the reminder; Telegram and iOS consume that persisted text. A deterministic fallback preserves reminder reliability on provider failure.

**Tech Stack:** Python 3.12, Anthropic SDK, SQLAlchemy async, FastAPI, SwiftUI, UserNotifications.

---

### Task 1: Persist generated delivery text

**Files:**
- Modify: `src/gwen/models.py`
- Modify: `src/gwen/repository.py`
- Modify: `tests/test_reminders.py`

- [ ] **Step 1: Write a failing repository test**

```python
created = await repository.add_reminder(42, "checar notificaciones", due_at, "America/Guatemala", "Junior, te recuerdo checar notificaciones.")
assert created.delivery_text == "Junior, te recuerdo checar notificaciones."
```

- [ ] **Step 2: Run the focused test**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_reminders.py -q -p no:cacheprovider`

Expected: FAIL because `add_reminder` does not accept `delivery_text`.

- [ ] **Step 3: Implement the smallest schema and repository change**

```python
delivery_text: Mapped[str] = mapped_column(Text)

async def add_reminder(self, user_id, content, due_at, timezone, delivery_text):
    reminder = Reminder(..., delivery_text=delivery_text)
```

- [ ] **Step 4: Re-run the focused test**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_reminders.py -q -p no:cacheprovider`

Expected: PASS.

### Task 2: Generate constrained Gwen wording with a safe fallback

**Files:**
- Modify: `src/gwen/assistant.py`
- Modify: `tests/test_assistant.py`

- [ ] **Step 1: Write failing assistant tests**

```python
assert answer == "Listo, Junior. Ya dejé ese recordatorio para las 09:00."
assert reminder.delivery_text == "Junior, te recuerdo pagar la renta."
```

And a provider failure test asserting the reminder still persists with the fallback delivery text.

- [ ] **Step 2: Run the focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_assistant.py -q -p no:cacheprovider`

Expected: FAIL because the assistant returns hard-coded wording and no delivery text exists.

- [ ] **Step 3: Implement constrained copy generation**

```python
async def reminder_messages(self, content, due_local) -> tuple[str, str]:
    # Request only a JSON acknowledgement and delivery message.
    # Validate both are non-empty and bounded; otherwise return fallback strings.
```

Only `content` and the already parsed local due time may be sent. Provider failure must return the fallback rather than prevent persistence.

- [ ] **Step 4: Re-run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_assistant.py -q -p no:cacheprovider`

Expected: PASS.

### Task 3: Reuse persisted wording in delivery and iPhone alerts

**Files:**
- Modify: `src/gwen/bot.py`
- Modify: `src/gwen/web.py`
- Modify: `gwen-ios/Models/ChatModels.swift`
- Modify: `gwen-ios/Services/LocalNotifications.swift`
- Modify: `gwen-iosTests/ContractTests.swift`
- Modify: `tests/test_web.py`

- [ ] **Step 1: Write failing contract tests**

```python
assert payload["reminders"][0]["delivery_text"] == "Junior, te recuerdo pagar la renta."
```

```swift
#expect(reminder.delivery_text == "Junior, te recuerdo pagar la renta.")
```

- [ ] **Step 2: Run backend and Swift contract tests**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_web.py -q -p no:cacheprovider`

Run: `TMPDIR=$PWD/.build/tmp DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer /Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swift test --scratch-path .build/Contracts`

Expected: FAIL because no delivery text is in the API or Swift model.

- [ ] **Step 3: Implement API and consumers**

```python
"delivery_text": item.delivery_text
```

```python
await self.application.bot.send_message(chat_id=self.allowed_user_id, text=reminder.delivery_text)
```

```swift
content.body = reminder.delivery_text
```

- [ ] **Step 4: Re-run contracts**

Expected: all focused tests PASS.

### Task 4: Full validation

**Files:**
- Modify: no production files

- [ ] **Step 1: Run backend tests and lint**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_reminders.py tests/test_assistant.py tests/test_web.py -q -p no:cacheprovider && .venv/bin/python -m ruff check src/gwen tests/test_reminders.py tests/test_assistant.py tests/test_web.py`

Expected: all focused tests pass and Ruff reports no errors.

- [ ] **Step 2: Build the iPhone target**

Run: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -quiet -project gwen-ios.xcodeproj -target gwen-ios -sdk iphoneos -configuration Debug build CODE_SIGNING_ALLOWED=NO SYMROOT=.build/Products OBJROOT=.build/Intermediates`

Expected: exit code 0.
