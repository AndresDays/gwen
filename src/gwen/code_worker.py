import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeWorkspace:
    id: str
    label: str
    path: Path
    checks: tuple[tuple[str, ...], ...]


class ClaudeCodeWorker:
    def __init__(self, config_path: Path, timeout_seconds: int = 600) -> None:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.workspaces = {
            item["id"]: CodeWorkspace(
                id=item["id"],
                label=item["label"],
                path=Path(item["path"]).resolve(strict=True),
                checks=tuple(tuple(command) for command in item.get("checks", [])),
            )
            for item in raw["workspaces"]
        }
        self.timeout_seconds = timeout_seconds

    def public_workspaces(self) -> list[dict[str, str]]:
        return [
            {"id": workspace.id, "label": workspace.label}
            for workspace in self.workspaces.values()
        ]

    def workspace(self, workspace_id: str) -> CodeWorkspace:
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError("Proyecto no autorizado.")
        return workspace

    async def _run(self, command: list[str], cwd: Path, timeout: int | None = None) -> str:
        startup = {"creationflags": 0x08000000} if os.name == "nt" else {}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **startup,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout or self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("La tarea de programación tardó demasiado.") from None
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail[-1200:] or "La tarea de programación falló.")
        return stdout.decode("utf-8", errors="replace").strip()

    async def plan(self, workspace_id: str, task: str) -> str:
        workspace = self.workspace(workspace_id)
        return await self._claude(workspace, task, edit=False)

    async def execute(
        self, workspace_id: str, task: str, commit: bool = False
    ) -> dict[str, object]:
        workspace = self.workspace(workspace_id)
        status = await self._run(["git", "status", "--porcelain"], workspace.path, 30)
        if status:
            raise RuntimeError("El proyecto tiene cambios pendientes; revísalos antes de ejecutar.")
        answer = await self._claude(workspace, task, edit=True)
        checks: list[str] = []
        for command in workspace.checks:
            await self._run(list(command), workspace.path)
            checks.append(" ".join(command))
        committed = False
        if commit:
            changed = await self._run(["git", "status", "--porcelain"], workspace.path, 30)
            if changed:
                await self._run(["git", "add", "-A"], workspace.path, 30)
                message = "Gwen: " + " ".join(task.strip().split())[:64]
                await self._run(["git", "commit", "-m", message], workspace.path, 60)
                committed = True
        diff = await self._run(["git", "diff", "--stat"], workspace.path, 30)
        return {"answer": answer, "checks": checks, "committed": committed, "diff": diff}

    @staticmethod
    def _claude_executable() -> str:
        executable = shutil.which("claude.exe") or shutil.which("claude")
        if os.name == "nt":
            launcher = shutil.which("claude.cmd")
            if launcher:
                native = (
                    Path(launcher).parent
                    / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
                )
                if native.is_file():
                    return str(native)
        if not executable:
            raise RuntimeError("Claude Code no está instalado.")
        return executable
    async def _claude(self, workspace: CodeWorkspace, task: str, edit: bool) -> str:
        prompt = task.strip()
        if not prompt:
            raise ValueError("La tarea está vacía.")
        tools = "Read,Glob,Grep,Edit,Write" if edit else "Read,Glob,Grep"
        mode = "acceptEdits" if edit else "plan"
        guardrail = (
            "Trabaja únicamente dentro del proyecto actual autorizado por Gwen. "
            "Nunca leas ni modifiques .env, credenciales, tokens, llaveros, certificados, "
            "bases de datos con datos personales ni archivos de autenticación. No uses red, "
            "no publiques y no ejecutes comandos. "
        )
        guardrail += (
            "Implementa la tarea solicitada con cambios mínimos y explica lo realizado."
            if edit
            else "No modifiques archivos. Devuelve un plan breve, riesgos y archivos afectados."
        )
        return await self._run(
            [
                self._claude_executable(), "-p", "--permission-mode", mode, "--tools", tools,
                "--disallowedTools",
                "Read(.env),Read(.env.*),Read(**/.env),Read(**/.env.*),Write(.env),Write(.env.*),Write(**/.env),Write(**/.env.*)",
                "--no-session-persistence", "--output-format", "text",
                "--append-system-prompt", guardrail, prompt,
            ],
            workspace.path,
        )
