import time
from pathlib import Path

from gwen.code_voice import (
    CodeContext,
    CodeContextStore,
    code_commit_requested,
    code_confirmation,
    code_followup_task,
    code_request_is_read_only,
    detect_code_request,
    detect_validation_request,
)


def test_voice_code_request_requires_project_and_programming_verb() -> None:
    assert detect_code_request("California es un proyecto importante") is None
    assert detect_code_request("Edita el botón") is None
    assert detect_code_request("¿Puedes ver el código de California?")[0] == "california"
    assert code_request_is_read_only("¿Puedes ver el código de California?")
    assert not code_request_is_read_only("Revisa y corrige el código de California")
    assert detect_code_request("Gwen, cambia mi viaje a California") is None
    assert detect_code_request("Gwen, trabaja en California y corrige el botón") == (
        "california",
        "Gwen, trabaja en California y corrige el botón",
    )
    assert detect_code_request(
        "en el proyecto California haz bump de los packages a versión 2.6.1"
    )[0] == "california"
    assert detect_code_request("Modifica el código de Gwen para mejorar las pruebas")[0] == "gwen"
    assert (
        detect_validation_request(
            "En el proyecto California hay unas pruebas que fallan, checa cuáles son porfi"
        )
        == "california"
    )
    assert (
        detect_validation_request("¿Cuáles pruebas fallaron en el proyecto de California?")
        == "california"
    )


def test_voice_code_confirmation_is_explicit_and_detects_commit() -> None:
    assert code_confirmation("quizá después") is None
    assert code_confirmation("no, cancela") == (False, False)
    assert code_confirmation("sí, ejecútalo") == (True, False)
    assert code_confirmation("sí, ejecútalo y haz commit") == (True, True)


def test_explicit_commit_negation_is_never_a_commit_request() -> None:
    context = CodeContext("california", time.time())
    for message in (
        "no hagas commit",
        "sin commit",
        "por favor no confirmes los cambios",
    ):
        assert not code_commit_requested(message)
        assert detect_code_request(message, context) is None


def test_followup_without_project_name_reuses_recent_context() -> None:
    context = CodeContext("california", time.time())
    assert detect_code_request("corrige esas pruebas que fallaron") is None
    assert detect_code_request("corrige esas pruebas que fallaron", context) == (
        "california",
        "corrige esas pruebas que fallaron",
    )
    assert detect_code_request("corrige directamente", context) == (
        "california",
        "corrige directamente",
    )
    assert detect_code_request("puedes hacer commit de los cambios que hay porfi", context) == (
        "california",
        "puedes hacer commit de los cambios que hay porfi",
    )
    assert detect_code_request("¿qué hora es?", context) is None


def test_stale_context_is_not_reused() -> None:
    stale = CodeContext("california", time.time() - 4000)
    assert detect_code_request("corrige esas pruebas", stale) is None
    assert detect_validation_request("checa las pruebas que fallan", stale) is None


def test_editing_verb_beats_validation_even_when_tests_are_mentioned() -> None:
    message = "en el proyecto california checa las pruebas que fallaron y corrígelas"
    assert detect_validation_request(message) is None
    assert detect_code_request(message) == ("california", message)
    assert not code_request_is_read_only(message)


def test_validation_without_project_name_uses_context() -> None:
    context = CodeContext("gwen", time.time())
    assert detect_validation_request("¿cuáles pruebas fallaron?") is None
    assert detect_validation_request("¿cuáles pruebas fallaron?", context) == "gwen"


def test_followup_task_carries_previous_failures() -> None:
    context = CodeContext("california", time.time(), ("FALLÓ: npm test — 2 failed",))
    task = code_followup_task("corrígelas", context)
    assert "corrígelas" in task
    assert "FALLÓ: npm test — 2 failed" in task
    assert code_followup_task("corrígelas", None) == "corrígelas"
    assert code_followup_task("corrígelas", CodeContext("california", time.time())) == "corrígelas"


def test_context_store_expires_and_clears() -> None:
    store = CodeContextStore()
    assert store.current() is None
    store.remember("california", ("FALLÓ: lint",))
    current = store.current()
    assert current is not None
    assert current.workspace_id == "california"
    assert current.last_failures == ("FALLÓ: lint",)
    store.clear()
    assert store.current() is None


def test_context_store_persists_active_project(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    store = CodeContextStore(path)
    store.remember("california", ("FALLÓ: lint",))

    restored = CodeContextStore(path).current()

    assert restored is not None
    assert restored.workspace_id == "california"
    assert restored.last_failures == ("FALLÓ: lint",)
