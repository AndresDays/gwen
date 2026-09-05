import asyncio
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_FILE_HEADER = re.compile(r"^(?:[A-Za-z]:)?[\\/]?[\w.@-]+(?:[\\/][\w.@ -]+)*\.[A-Za-z]+$")
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:)?[\\/](?:[^\\/\s]+[\\/])+")
_JEST_FAIL = re.compile(r"^FAIL\s+(?P<file>\S+)$")
_JEST_COUNTS = re.compile(r"^Tests:\s+(?P<failed>\d+) failed")
_LOCATION = re.compile(r"^(?P<file>\S+?):(?P<line>\d+):\d+\s+(?P<message>.+)$")
_RULE_SUFFIX = re.compile(r"\s+[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:/[a-z0-9-]+)?$")
_ESLINT_ERROR = re.compile(r"^(?P<line>\d+):(?P<column>\d+)\s+error\s+(?P<message>.+)$")


# Bloqueo real, no sólo texto en el prompt: la terminal queda abierta para las
# validaciones, pero nunca para reescribir la historia de git ni tocar secretos.
_DISALLOWED = ",".join(
    (
        "Read(.env)",
        "Read(.env.*)",
        "Read(**/.env)",
        "Read(**/.env.*)",
        "Write(.env)",
        "Write(.env.*)",
        "Write(**/.env)",
        "Write(**/.env.*)",
        "Bash(git commit:*)",
        "Bash(git push:*)",
        "Bash(git reset:*)",
        "Bash(git checkout:*)",
        "Bash(git restore:*)",
        "Bash(git clean:*)",
        "Bash(git stash:*)",
        "Bash(rm:*)",
        "Bash(del:*)",
        "Bash(curl:*)",
        "Bash(wget:*)",
    )
)


def changed_files(status: str) -> tuple[str, ...]:
    """Rutas de `git status --porcelain`, tolerando que la salida venga sin sangría."""
    files: list[str] = []
    for line in status.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1]:
            files.append(parts[1])
    return tuple(files)


class WorkspaceHasForeignChanges(RuntimeError):
    """El repositorio tiene cambios que Gwen no registró en su sesión."""

    def __init__(self, workspace_id: str, label: str, files: tuple[str, ...]) -> None:
        self.workspace_id = workspace_id
        self.label = label
        self.files = files
        super().__init__(f"{label} tiene cambios ajenos a la sesión de Gwen.")

    def message(self) -> str:
        listed = ", ".join(self.files[:6])
        if len(self.files) > 6:
            listed += f" y {len(self.files) - 6} más"
        return (
            f"No toqué {self.label}: el repositorio tiene cambios que yo no hice "
            f"({listed}). Haz commit o descártalos, o dime «adopta los cambios de "
            f"{self.workspace_id}» para que los tome como punto de partida."
        )


class CodeTaskTimeout(RuntimeError):
    """La tarea excedió el tiempo máximo permitido."""


@dataclass(frozen=True)
class CodeWorkspace:
    id: str
    label: str
    path: Path
    checks: tuple[tuple[str, ...], ...]
    adopt_foreign_changes: bool = False
    timeout_seconds: int | None = None


def _humanize_failure(piece: str) -> str | None:
    """Convierte una línea de herramienta en algo que se pueda decir en voz alta."""
    piece = _ABSOLUTE_PATH.sub("", " ".join(piece.split()))
    if not piece or piece.startswith(("● ", "Test Suites:")):
        return None  # nombres de casos y conteos de suites: ruido para un resumen
    counts = _JEST_COUNTS.match(piece)
    if counts:
        total = int(counts["failed"])
        return f"{total} prueba fallando" if total == 1 else f"{total} pruebas fallando"
    failing = _JEST_FAIL.match(piece)
    if failing:
        name = failing["file"].replace("\\", "/").rsplit("/", 1)[-1]
        return name.removesuffix(".test.js").removesuffix(".test.jsx")
    located = _LOCATION.match(piece)
    if located:
        name = located["file"].replace("\\", "/").rsplit("/", 1)[-1]
        message = _RULE_SUFFIX.sub("", located["message"]).strip() or located["message"]
        return f"{name} línea {located['line']}: {message}"
    return piece[:120]


def brief_failures(checks: list[object], limit: int = 200) -> str:
    """Resumen corto y hablable: sin comandos, sin rutas absolutas, sin volcados."""
    parts: list[str] = []
    for check in checks:
        text = str(check)
        if not text.startswith("FALLÓ:"):
            continue
        detail = text.split(" — ", 1)[1] if " — " in text else text[len("FALLÓ:") :]
        for piece in detail.split(" | "):
            humanized = _humanize_failure(piece)
            if humanized and humanized not in parts:
                parts.append(humanized)
    summary = ""
    shown = 0
    for part in parts:
        candidate = f"{summary}; {part}" if summary else part
        if len(candidate) > limit:
            break
        summary = candidate
        shown += 1
    if summary and len(parts) > shown:
        summary += f", y {len(parts) - shown} más"
    return summary or "sin detalle disponible"


def failures_prompt(checks: tuple[tuple[str, ...], ...]) -> str:
    """Instrucciones para que Claude Code descubra y verifique los errores él mismo."""
    commands = "\n".join(f"- {' '.join(command)}" for command in checks)
    return (
        "Corre estos comandos para ver los errores reales y arreglarlos:\n"
        f"{commands}\n"
        "Corrige solo lo que reporten como error, vuelve a correrlos para confirmar, "
        "y no hagas commit."
    )


def code_result_answer(result: dict[str, object]) -> str:
    if result.get("nothing_to_fix"):
        return "Revisé el proyecto y no hay nada que corregir: lint y pruebas ya pasan."
    failures = brief_failures(list(result["checks"]))
    if result["committed"]:
        return "Listo, ya está hecho y también creé el commit."
    if not result.get("changed", True):
        # Sin archivos tocados no hubo cambio, por más que las validaciones corrieran.
        explanation = " ".join(str(result.get("answer") or "").split())[:220]
        answer = "No cambié ningún archivo."
        if explanation:
            answer += f" {explanation}"
        return answer
    if not result["validation_passed"]:
        return f"Hice el cambio, pero sigue fallando: {failures}. No hice commit."
    if result["committed"]:
        return "Listo, ya está hecho y también creé el commit."
    return "Listo, ya está hecho y todo pasa. No hice commit porque no me lo pediste."


def validation_result_answer(result: dict[str, object]) -> str:
    checks = list(result["checks"])
    if not any(str(check).startswith("FALLÓ:") for check in checks):
        return "Revisé el proyecto y todo está pasando."
    return f"Está fallando: {brief_failures(checks)}."


def commit_result_answer(result: dict[str, object]) -> str:
    """Informa sólo pruebas, archivos y hash que el trabajador comprobó."""
    if not result["validation_passed"]:
        return f"No creé el commit: {brief_failures(list(result['checks']))}."
    if not result["committed"]:
        return "No creé un commit: no hay cambios pendientes."
    files = ", ".join(str(item) for item in result["files"])
    commit_hash = str(result["commit"])
    checks = "; ".join(str(item) for item in result["checks"]) or "sin validaciones configuradas"
    return f"Commit creado: {commit_hash}. Archivos: {files}. Validaciones: {checks}."


class ClaudeCodeWorker:
    def __init__(self, config_path: Path, timeout_seconds: int = 2700) -> None:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.workspaces = {
            item["id"]: CodeWorkspace(
                id=item["id"],
                label=item["label"],
                path=Path(item["path"]).resolve(strict=True),
                checks=tuple(tuple(command) for command in item.get("checks", [])),
                adopt_foreign_changes=bool(item.get("adopt_foreign_changes", False)),
                timeout_seconds=item.get("timeout_seconds"),
            )
            for item in raw["workspaces"]
        }
        self.timeout_seconds = timeout_seconds
        self.state_path = config_path.parent / ".gwen-code-state.json"
        self._lock = asyncio.Lock()

    def _state(self) -> dict[str, str]:
        if not self.state_path.is_file():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def _save_state(self, state: dict[str, str]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    async def _working_tree(self, workspace: CodeWorkspace) -> tuple[str, str]:
        status = await self._run(["git", "status", "--porcelain"], workspace.path, 30)
        diff = await self._run(["git", "diff", "--binary"], workspace.path, 30)
        fingerprint = hashlib.sha256(f"{status}\0{diff}".encode()).hexdigest()
        return status, fingerprint

    async def adopt_current_changes(self, workspace_id: str) -> None:
        workspace = self.workspace(workspace_id)
        status, fingerprint = await self._working_tree(workspace)
        state = self._state()
        if status:
            state[workspace_id] = fingerprint
        else:
            state.pop(workspace_id, None)
        self._save_state(state)

    def public_workspaces(self) -> list[dict[str, str]]:
        return [
            {"id": workspace.id, "label": workspace.label} for workspace in self.workspaces.values()
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
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise CodeTaskTimeout(
                "La tarea tardó más de lo permitido y la detuve. "
                "Puede que haya quedado trabajo a medias en el proyecto."
            ) from None
        if process.returncode != 0:
            output = stdout.decode("utf-8", errors="replace").strip()
            errors = stderr.decode("utf-8", errors="replace").strip()
            detail = "\n".join(part for part in (output, errors) if part)
            # ESLint alinea columnas con cientos de espacios: 230 KB de salida de
            # los que 120 KB son relleno. Sin quitarlo, el corte se comía el
            # único error real, que estaba en el carácter 218 000.
            detail = "\n".join(line.rstrip() for line in detail.splitlines())
            raise RuntimeError(detail[:2_000_000] or "La tarea de programación falló.")
        return stdout.decode("utf-8", errors="replace").strip()

    async def plan(self, workspace_id: str, task: str) -> str:
        workspace = self.workspace(workspace_id)
        return await self._claude(workspace, task, edit=False)

    async def inspect(self, workspace_id: str, task: str) -> str:
        workspace = self.workspace(workspace_id)
        return await self._claude(workspace, task, edit=False, inspection=True)

    async def _run_checks(self, workspace: CodeWorkspace) -> tuple[list[str], bool]:
        checks: list[str] = []
        passed = True
        for command in workspace.checks:
            try:
                await self._run(list(command), workspace.path)
                checks.append("OK: " + " ".join(command))
            except CodeTaskTimeout:
                raise
            except RuntimeError as error:
                passed = False
                summary = self._validation_failure_summary(str(error))
                checks.append("FALLÓ: " + " ".join(command) + " — " + summary)
        return checks, passed

    async def validate(self, workspace_id: str) -> dict[str, object]:
        workspace = self.workspace(workspace_id)
        async with self._lock:
            checks, validation_passed = await self._run_checks(workspace)
            await self.adopt_current_changes(workspace_id)
            return {"checks": checks, "validation_passed": validation_passed}

    async def commit(self, workspace_id: str) -> dict[str, object]:
        """Valida y confirma cambios pendientes sin iniciar Claude Code."""
        workspace = self.workspace(workspace_id)
        async with self._lock:
            status, fingerprint = await self._working_tree(workspace)
            expected = self._state().get(workspace_id)
            if status and expected != fingerprint:
                if workspace.adopt_foreign_changes:
                    await self.adopt_current_changes(workspace_id)
                else:
                    raise WorkspaceHasForeignChanges(
                        workspace_id, workspace.label, changed_files(status)
                    )
            if not status:
                return {
                    "checks": [],
                    "validation_passed": True,
                    "committed": False,
                    "files": (),
                    "commit": None,
                }
            checks, validation_passed = await self._run_checks(workspace)
            if not validation_passed:
                await self.adopt_current_changes(workspace_id)
                return {
                    "checks": checks,
                    "validation_passed": False,
                    "committed": False,
                    "files": (),
                    "commit": None,
                }
            await self._run(["git", "add", "-A"], workspace.path, 30)
            files = tuple(
                item
                for item in (
                    await self._run(["git", "diff", "--cached", "--name-only"], workspace.path, 30)
                ).splitlines()
                if item
            )
            if not files:
                return {
                    "checks": checks,
                    "validation_passed": True,
                    "committed": False,
                    "files": (),
                    "commit": None,
                }
            await self._run(
                ["git", "commit", "-m", "Gwen: guardar cambios pendientes"], workspace.path, 60
            )
            commit_hash = await self._run(["git", "rev-parse", "HEAD"], workspace.path, 30)
            await self.adopt_current_changes(workspace_id)
            return {
                "checks": checks,
                "validation_passed": True,
                "committed": True,
                "files": files,
                "commit": commit_hash,
            }

    async def execute(
        self, workspace_id: str, task: str, commit: bool = False, diagnose: bool = False
    ) -> dict[str, object]:
        workspace = self.workspace(workspace_id)
        async with self._lock:
            status, fingerprint = await self._working_tree(workspace)
            state = self._state()
            expected = state.get(workspace_id)
            if status and expected != fingerprint:
                if workspace.adopt_foreign_changes:
                    await self.adopt_current_changes(workspace_id)
                else:
                    raise WorkspaceHasForeignChanges(
                        workspace_id, workspace.label, changed_files(status)
                    )
            before = fingerprint
            if diagnose:
                # Claude Code tiene terminal: corre él mismo las validaciones, que es
                # más rápido que hacerlo aquí antes y después.
                task = f"{task}\n\n{failures_prompt(workspace.checks)}"
            try:
                answer = await self._claude(workspace, task, edit=True)
            except CodeTaskTimeout:
                # El trabajo a medias sigue en el disco: nombrarlo evita que se pierda.
                partial, _ = await self._working_tree(workspace)
                await self.adopt_current_changes(workspace_id)
                touched = changed_files(partial)
                detail = f" Quedaron tocados: {', '.join(touched[:5])}." if touched else ""
                raise CodeTaskTimeout(
                    f"Se pasó del tiempo límite y lo detuve.{detail} "
                    "Dime si quieres que siga desde ahí."
                ) from None
            finally:
                await self.adopt_current_changes(workspace_id)
            _, after = await self._working_tree(workspace)
            changed_anything = after != before
            checks, validation_passed = await self._run_checks(workspace)
            await self.adopt_current_changes(workspace_id)
            committed = False
            if commit and validation_passed:
                changed = await self._run(["git", "status", "--porcelain"], workspace.path, 30)
                if changed:
                    await self._run(["git", "add", "-A"], workspace.path, 30)
                    message = "Gwen: " + " ".join(task.strip().split())[:64]
                    await self._run(["git", "commit", "-m", message], workspace.path, 60)
                    committed = True
                    await self.adopt_current_changes(workspace_id)
            diff = await self._run(["git", "diff", "--stat"], workspace.path, 30)
            return {
                "answer": answer,
                "checks": checks,
                "validation_passed": validation_passed,
                "committed": committed,
                "changed": changed_anything,
                "diff": diff,
            }

    @staticmethod
    def _validation_failure_summary(output: str) -> str:
        """Extrae los errores accionables con su archivo, no fragmentos de código sueltos.

        La salida de ESLint trae cientos de warnings y una sola línea de severidad
        `error`; además muchos warnings citan código con la palabra "error" dentro
        (`const { data, error } = ...`). Filtrar por esa palabra devolvía el
        fragmento equivocado y sin archivo, así que Claude Code no podía arreglarlo.
        """
        raw = output.splitlines()
        eslint = ClaudeCodeWorker._eslint_errors(raw)
        if eslint:
            return ClaudeCodeWorker._capped(eslint)
        jest = [
            " ".join(line.split())
            for line in raw
            if line.lstrip().startswith(("FAIL ", "● ", "Test Suites:", "Tests:", "Error:"))
            and "● Console" not in line
        ]
        if jest:
            return ClaudeCodeWorker._capped(jest)
        tail = [" ".join(line.split()) for line in raw if line.strip()][-3:]
        return ClaudeCodeWorker._capped(tail) or "La validación terminó con error."

    @staticmethod
    def _eslint_errors(raw: list[str]) -> list[str]:
        """Devuelve `archivo:línea:col error mensaje (regla)` por cada error real."""
        errors: list[str] = []
        current_file = ""
        for line in raw:
            stripped = line.strip()
            if _FILE_HEADER.match(stripped):
                current_file = stripped
                continue
            match = _ESLINT_ERROR.match(stripped)
            if match is None:
                continue
            place = f"{current_file}:" if current_file else ""
            message = " ".join(match["message"].split())
            errors.append(f"{place}{match['line']}:{match['column']} {message}")
        return errors

    @staticmethod
    def _capped(parts: list[str]) -> str:
        summary = ""
        for part in parts:
            candidate = f"{summary} | {part}" if summary else part
            if len(candidate) > 1200:
                remaining = len(parts) - parts.index(part)
                return f"{summary} | y {remaining} más" if summary else candidate[:1200]
            summary = candidate
        return summary

    @staticmethod
    def _claude_executable() -> str:
        executable = shutil.which("claude.exe") or shutil.which("claude")
        if os.name == "nt":
            launcher = shutil.which("claude.cmd")
            if launcher:
                native = (
                    Path(launcher).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
                )
                if native.is_file():
                    return str(native)
        if not executable:
            raise RuntimeError("Claude Code no está instalado.")
        return executable

    async def _claude(
        self, workspace: CodeWorkspace, task: str, edit: bool, inspection: bool = False
    ) -> str:
        prompt = task.strip()
        if not prompt:
            raise ValueError("La tarea está vacía.")
        tools = "Read,Glob,Grep,Edit,Write,Bash" if edit else "Read,Glob,Grep"
        mode = "acceptEdits" if edit else "plan"
        guardrail = (
            "Trabaja únicamente dentro del proyecto actual autorizado por Gwen. "
            "Nunca leas ni modifiques .env, credenciales, tokens, llaveros, certificados, "
            "bases de datos con datos personales ni archivos de autenticación. No uses red "
            "y no publiques nada. "
        )
        if edit:
            commands = " o ".join(" ".join(command) for command in workspace.checks) or "ninguno"
            guardrail += (
                "Tienes terminal: corre las validaciones del proyecto para ver los errores "
                f"reales y para comprobar tu arreglo. Comandos de validación: {commands}. "
                "Ve directo al grano: corre la validación, lee el error, corrígelo y vuelve a "
                "correrla. No explores el repositorio de más ni pidas permiso. "
                "Nunca ejecutes git commit, git push, git reset, git checkout ni nada que "
                "descarte cambios; tampoco instales dependencias ni borres archivos. "
                "Implementa la tarea con cambios mínimos y explica en una o dos frases "
                "qué cambiaste."
            )
        elif inspection:
            guardrail += (
                "No modifiques archivos. Responde brevemente en lenguaje natural, "
                "sin mostrar bloques ni volcar código fuente."
            )
        else:
            guardrail += (
                "No modifiques archivos. Devuelve un plan breve, riesgos y archivos afectados."
            )
        return await self._run(
            [
                self._claude_executable(),
                "-p",
                "--permission-mode",
                mode,
                "--tools",
                tools,
                "--allowedTools",
                tools,
                "--disallowedTools",
                _DISALLOWED,
                "--no-session-persistence",
                "--output-format",
                "text",
                "--append-system-prompt",
                guardrail,
                prompt,
            ],
            workspace.path,
            timeout=workspace.timeout_seconds,
        )
