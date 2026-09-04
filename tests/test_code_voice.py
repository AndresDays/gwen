from gwen.code_voice import code_confirmation, detect_code_request


def test_voice_code_request_requires_project_and_programming_verb() -> None:
    assert detect_code_request("California es un proyecto importante") is None
    assert detect_code_request("Edita el botón") is None
    assert detect_code_request("Gwen, cambia mi viaje a California") is None
    assert detect_code_request("Gwen, trabaja en California y corrige el botón") == (
        "california",
        "Gwen, trabaja en California y corrige el botón",
    )
    assert detect_code_request("Modifica el código de Gwen para mejorar las pruebas")[0] == "gwen"


def test_voice_code_confirmation_is_explicit_and_detects_commit() -> None:
    assert code_confirmation("quizá después") is None
    assert code_confirmation("no, cancela") == (False, False)
    assert code_confirmation("sí, ejecútalo") == (True, False)
    assert code_confirmation("sí, ejecútalo y haz commit") == (True, True)
