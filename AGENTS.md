# Gwen project instructions

Before taking action, read `CONTEXT.md` completely and treat it as the current
handoff for this project. Never print or expose values from `.env`; validate
secrets only as present/absent. Preserve the private single-user security model.

After code changes, run Ruff and the test suite. Keep Telegram HTTP logging at
WARNING or higher because authenticated Telegram URLs contain the bot token.
