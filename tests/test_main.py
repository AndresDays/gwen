import logging

from gwen.main import configure_logging


def test_http_loggers_never_emit_info() -> None:
    configure_logging()

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
