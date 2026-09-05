"""Estado local, mínimo y verificable de la última tarea de programación."""

import json
import time
from pathlib import Path


class CodeTaskStore:
    """Persiste etapas y evidencia, sin guardar la orden original ni código fuente."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._task = self._load()

    def _load(self) -> dict[str, object]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._task, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def start(self, workspace: str, action: str) -> None:
        self._task = {
            "workspace": workspace,
            "action": action,
            "stage": "iniciada",
            "started_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        self._save()

    def update(self, stage: str, **evidence: object) -> None:
        if not self._task:
            return
        self._task["stage"] = stage
        self._task["updated_at"] = int(time.time())
        self._task.update({key: value for key, value in evidence.items() if value is not None})
        self._save()

    def snapshot(self) -> dict[str, object]:
        return dict(self._task)
