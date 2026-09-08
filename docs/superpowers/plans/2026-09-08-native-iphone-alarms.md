# Native iPhone Alarms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Gwen create and cancel AlarmKit alarms owned by the iPhone app, without access to Apple Clock alarms.

**Architecture:** The backend validates an explicit alarm request through an opt-in action contract and returns a pending action only to an iPhone advertising `alarm.create.v1`. The phone authorizes AlarmKit, schedules/cancels app-owned alarms, and keeps the action-to-alarm mapping in protected local storage.

**Tech Stack:** FastAPI, Pydantic, Anthropic tool use, SwiftUI, AlarmKit, UserDefaults/FileProtection.

---

### Task 1: Define and validate the backend alarm action

**Files:**
- Create: `src/gwen/alarm_actions.py`
- Modify: `src/gwen/web.py`
- Modify: `src/gwen/realtime.py`
- Test: `tests/test_alarm_actions.py`

- [ ] Write failing tests for a future one-time `alarm.create` and a `alarm.cancel` targeting a Gwen action ID.
- [ ] Implement a Pydantic action contract and an explicit-only classifier with `alarm.create.v1`.
- [ ] Expose actions only when the device capability header includes the alarm capability.
- [ ] Run focused backend tests.

### Task 2: Schedule and cancel app-owned alarms

**Files:**
- Create: `gwen-ios/Services/AlarmService.swift`
- Create: `gwen-ios/Storage/AlarmJournal.swift`
- Modify: `gwen-ios/Models/ChatModels.swift`
- Modify: `gwen-ios/Models/ChatController.swift`
- Modify: `Gwen-Info.plist`
- Test: `gwen-iosTests/ContractTests.swift`

- [ ] Write failing Swift decoding and journal tests.
- [ ] Add `NSAlarmKitUsageDescription` and an AlarmKit service that requests authorization, schedules, and cancels only Gwen IDs.
- [ ] Persist UUID mappings locally with complete file protection.
- [ ] Apply actions after an ordinary chat reply and surface a truthful result in chat.
- [ ] Run Swift contracts and iPhone target build.

### Task 3: Verify boundaries

**Files:**
- Modify: no production files

- [ ] Confirm no API touches Clock.app alarms; only IDs in Gwen's journal are cancellable.
- [ ] Run focused backend tests/lint and Swift tests/build.
