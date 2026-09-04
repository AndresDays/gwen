import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gwen.code_worker import ClaudeCodeWorker


def make_worker(tmp_path: Path) -> ClaudeCodeWorker:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "workspaces.json"
    config.write_text(
        json.dumps({"workspaces": [{"id": "project", "label": "Project", "path": str(project)}]}),
        encoding="utf-8",
    )
    return ClaudeCodeWorker(config)


def test_worker_exposes_labels_without_private_paths(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)
    assert worker.public_workspaces() == [{"id": "project", "label": "Project"}]
    with pytest.raises(ValueError, match="no autorizado"):
        worker.workspace("other")


@pytest.mark.asyncio
async def test_worker_refuses_to_edit_a_dirty_repository(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)
    worker._run = AsyncMock(return_value=" M existing.py")
    worker._claude = AsyncMock()
    with pytest.raises(RuntimeError, match="cambios pendientes"):
        await worker.execute("project", "Haz un cambio")
    worker._claude.assert_not_awaited()
