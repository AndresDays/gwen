from gwen.assistant import current_context


def test_current_context_uses_guatemala_time() -> None:
    context = current_context()
    assert "America/Guatemala" in context
    assert "Fecha y hora actuales:" in context
    assert "No adivines el año actual" in context
