import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gwen.code_worker import (
    ClaudeCodeWorker,
    brief_failures,
    changed_files,
    code_result_answer,
    commit_result_answer,
)


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
    with pytest.raises(RuntimeError, match="cambios ajenos"):
        await worker.execute("project", "Haz un cambio")
    worker._claude.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_accumulates_its_own_pending_changes(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)

    async def git_result(command: list[str], *_: object, **__: object) -> str:
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M existing.py"
        if command[:3] == ["git", "diff", "--binary"]:
            return "owned diff"
        if command[:3] == ["git", "diff", "--stat"]:
            return "existing.py | 1 +"
        return ""

    worker._run = AsyncMock(side_effect=git_result)
    await worker.adopt_current_changes("project")
    worker._claude = AsyncMock(return_value="Cambio hecho")

    result = await worker.execute("project", "Haz otro cambio")

    assert result["committed"] is False
    assert result["validation_passed"] is True
    worker._claude.assert_awaited_once()


def test_validation_failure_summary_keeps_test_names_without_source_dump() -> None:
    output = """
    FAIL src/example.test.js
    irrelevant source line
    Tests: 2 failed, 10 passed, 12 total
    Test Suites: 1 failed, 4 passed, 5 total
    """
    summary = ClaudeCodeWorker._validation_failure_summary(output)
    assert "FAIL src/example.test.js" in summary
    assert "Tests: 2 failed" in summary
    assert "irrelevant source line" not in summary


@pytest.mark.asyncio
async def test_worker_can_run_validation_without_claude(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)
    worker._run = AsyncMock(return_value="")
    worker._claude = AsyncMock()

    result = await worker.validate("project")

    assert result == {"checks": [], "validation_passed": True}
    worker._claude.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_commits_pending_changes_without_claude(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)

    async def git_result(command: list[str], *_: object, **__: object) -> str:
        if command[:3] == ["git", "status", "--porcelain"]:
            return " M example.py"
        if command[:3] == ["git", "diff", "--binary"]:
            return "diff"
        if command == ["git", "diff", "--cached", "--name-only"]:
            return "example.py"
        if command == ["git", "rev-parse", "HEAD"]:
            return "abc123"
        return ""

    worker._run = AsyncMock(side_effect=git_result)
    await worker.adopt_current_changes("project")
    worker._claude = AsyncMock()

    result = await worker.commit("project")

    assert result["committed"] is True
    assert result["files"] == ("example.py",)
    assert result["commit"] == "abc123"
    worker._claude.assert_not_awaited()
    assert "abc123" in commit_result_answer(result)


def test_eslint_summary_reports_the_real_error_not_a_warning_fragment() -> None:
    output = """
C:/proyecto/src/pages/visor.jsx
  3758:5   error    'descargarReportePdf' is not defined                     no-undef

C:/proyecto/src/components/editar.jsx
  55:11  warning  Error: Cannot access variable before it is declared
> 52 |     const { data, error } = await supabase
  147 |     const { data, error } = await supabase                react-hooks/immutability

C:/proyecto/src/utils/ticket.js
  49:7  warning  'generarCodigo' is assigned a value but never used         no-unused-vars

* 256 problems (1 error, 255 warnings)
"""
    summary = ClaudeCodeWorker._validation_failure_summary(output)
    assert "visor.jsx:3758:5" in summary
    assert "'descargarReportePdf' is not defined" in summary
    assert "no-undef" in summary
    # El fragmento de código de un warning ya no secuestra el resumen.
    assert "const { data, error }" not in summary
    assert "no-unused-vars" not in summary


def test_jest_summary_keeps_failing_suites_and_counts() -> None:
    output = """
PASS src/utils/otro.test.js
FAIL src/utils/importar-informe-visitas.test.js
    expect(received).toEqual(expected)
FAIL src/utils/generarTicketVenta.test.js
Test Suites: 2 failed, 161 passed, 163 total
Tests:       2 failed, 1504 passed, 1506 total
"""
    summary = ClaudeCodeWorker._validation_failure_summary(output)
    assert "FAIL src/utils/importar-informe-visitas.test.js" in summary
    assert "FAIL src/utils/generarTicketVenta.test.js" in summary
    assert "Tests: 2 failed, 1504 passed, 1506 total" in summary
    assert "PASS" not in summary


def test_summary_falls_back_to_the_tail_and_stays_bounded() -> None:
    assert (
        ClaudeCodeWorker._validation_failure_summary("boom\nalgo salió mal")
        == "boom | algo salió mal"
    )
    assert ClaudeCodeWorker._validation_failure_summary("") == "La validación terminó con error."
    crowded = "\n".join(
        f"C:/p/f{index}.js\n  {index}:1  error  fallo número {index} con detalle largo  regla"
        for index in range(200)
    )
    assert len(ClaudeCodeWorker._validation_failure_summary(crowded)) <= 1210


def test_changed_files_keeps_the_first_path_intact() -> None:
    # git status llega sin la sangría inicial de la primera línea; el corte fijo
    # se comía la primera letra ("ackage-lock.json").
    status = "M  package-lock.json\n M package.json\n?? nuevo.txt\nR  viejo.js -> nuevo.js"
    assert changed_files(status) == (
        "package-lock.json",
        "package.json",
        "nuevo.txt",
        "viejo.js -> nuevo.js",
    )
    assert changed_files("") == ()


@pytest.mark.asyncio
async def test_adopt_foreign_changes_lets_the_worker_proceed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "workspaces.json"
    config.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "project",
                        "label": "Project",
                        "path": str(project),
                        "adopt_foreign_changes": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    worker = ClaudeCodeWorker(config)
    worker._run = AsyncMock(return_value=" M ajeno.py")
    worker._claude = AsyncMock(return_value="Hecho")
    result = await worker.execute("project", "Haz un cambio")
    assert result["answer"] == "Hecho"
    worker._claude.assert_awaited_once()


def test_result_answer_admits_when_nothing_changed() -> None:
    result = {
        "answer": "No encontré la definición de descargarReportePdf, no toqué nada.",
        "checks": ["FALLÓ: npm test — FAIL src/a.test.js"],
        "validation_passed": False,
        "committed": False,
        "changed": False,
        "diff": "",
    }
    answer = code_result_answer(result)
    assert "No cambié ningún archivo" in answer
    assert "No encontré la definición" in answer
    assert "Hice el cambio" not in answer

def test_result_answer_reports_a_commit_even_when_claude_did_not_edit() -> None:
    result = {
        "answer": "El commit fue denegado por permisos.",
        "checks": ["OK: npm test"],
        "validation_passed": True,
        "committed": True,
        "changed": False,
        "diff": "",
    }
    answer = code_result_answer(result)
    assert "creé el commit" in answer
    assert "denegado" not in answer



def test_result_answer_still_reports_real_changes() -> None:
    result = {
        "answer": "Listo",
        "checks": ["FALLÓ: npm test — FAIL src/a.test.js"],
        "validation_passed": False,
        "committed": False,
        "changed": True,
        "diff": "a.js | 2 +-",
    }
    answer = code_result_answer(result)
    assert "Hice el cambio" in answer
    assert "npm test" not in answer


def test_eslint_padding_does_not_push_the_error_past_the_cap() -> None:
    padding = " " * 400
    noise = "\n".join(
        f"C:/p/warn{index}.js\n  {index}:1  warning  algo{padding}una-regla" for index in range(600)
    )
    output = f"{noise}\nC:/p/real.js\n  10:5  error  'x' is not defined{padding}no-undef"
    trimmed = "\n".join(line.rstrip() for line in output.splitlines())
    assert len(output) > 200_000
    summary = ClaudeCodeWorker._validation_failure_summary(trimmed[:2_000_000])
    assert "real.js:10:5" in summary
    assert "no-undef" in summary


def test_brief_failures_drops_commands_paths_and_noise() -> None:
    checks = [
        "OK: npm.cmd run build",
        "FALLÓ: npm.cmd run lint — "
        "C:/Programacion/californIA/src/pages/visor-dicom.jsx:3758:5 "
        "'descargarReportePdf' is not defined no-undef",
        "FALLÓ: npm.cmd test -- --runInBand — "
        "FAIL src/utils/generarTicketVenta.test.js | ● Console | ● Console | "
        "Test Suites: 2 failed, 161 passed | Tests: 2 failed, 1504 passed, 1506 total",
    ]
    summary = brief_failures(checks)
    assert "visor-dicom.jsx línea 3758: 'descargarReportePdf' is not defined" in summary
    assert "generarTicketVenta" in summary
    assert "2 pruebas fallando" in summary
    # Nada de comandos, rutas absolutas, reglas ni ruido de consola.
    assert "npm.cmd" not in summary
    assert "Programacion" not in summary
    assert "no-undef" not in summary
    assert "Console" not in summary
    assert "FAIL" not in summary
    assert "Test Suites" not in summary
    assert len(summary) <= 260


def test_brief_failures_singular_and_empty_cases() -> None:
    assert "1 prueba fallando" in brief_failures(["FALLÓ: jest — Tests: 1 failed, 9 passed"])
    assert brief_failures(["OK: npm test"]) == "sin detalle disponible"


@pytest.mark.asyncio
async def test_diagnose_hands_the_real_errors_to_claude_code(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "workspaces.json"
    config.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "project",
                        "label": "Project",
                        "path": str(project),
                        "checks": [["fake-lint"]],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    worker = ClaudeCodeWorker(config)
    worker._run = AsyncMock(return_value="")
    worker._claude = AsyncMock(return_value="Definí la función")
    await worker.execute("project", "corrige los errores de lint", diagnose=True)
    prompt = worker._claude.await_args.args[1]
    # Claude Code recibe los comandos de validación para correrlos él mismo.
    assert "fake-lint" in prompt
    assert "vuelve a correrlos para confirmar" in prompt
    assert "no hagas commit" in prompt


@pytest.mark.asyncio
async def test_claude_gets_a_terminal_but_never_destructive_commands(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)
    captured: list[list[str]] = []

    async def run(command: list[str], *_: object, **__: object) -> str:
        captured.append(command)
        return ""

    worker._run = run
    await worker.execute("project", "arregla el lint")
    invocation = next(cmd for cmd in captured if "--tools" in cmd)
    tools = invocation[invocation.index("--tools") + 1]
    blocked = invocation[invocation.index("--disallowedTools") + 1]
    assert "Bash" in tools
    # Autorizado de verdad, no sólo pedido en el prompt.
    assert invocation[invocation.index("--allowedTools") + 1] == tools
    for forbidden in ("Bash(git commit:*)", "Bash(git push:*)", "Bash(rm:*)", "Read(.env)"):
        assert forbidden in blocked


@pytest.mark.asyncio
async def test_inspection_never_gets_a_terminal(tmp_path: Path) -> None:
    worker = make_worker(tmp_path)
    captured: list[list[str]] = []

    async def run(command: list[str], *_: object, **__: object) -> str:
        captured.append(command)
        return ""

    worker._run = run
    await worker.inspect("project", "revisa el login")
    invocation = next(cmd for cmd in captured if "--tools" in cmd)
    assert "Bash" not in invocation[invocation.index("--tools") + 1]
