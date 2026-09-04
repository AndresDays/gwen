from gwen.memory import explicit_memory_request, is_safe_memory


def test_natural_memory_request_is_detected() -> None:
    request = explicit_memory_request("Recuerda que prefiero café sin azúcar")
    assert request is not None
    assert request.safe
    assert request.content == "prefiero café sin azúcar"


def test_sensitive_memory_is_rejected() -> None:
    request = explicit_memory_request("recuerda que mi contraseña es hunter2")
    assert request is not None
    assert not request.safe
    assert not is_safe_memory("mi tarjeta es 4111 1111 1111 1111")


def test_regular_conversation_is_not_memory() -> None:
    assert explicit_memory_request("¿Recuerdas qué café prefiero?") is None
