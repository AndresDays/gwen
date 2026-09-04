import re
from dataclasses import dataclass

_MEMORY_PATTERN = re.compile(
    r"^(?:por favor,?\s*)?(?:recuerda|recordá|remember)(?:\s+que)?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_WORDS = re.compile(
    r"\b(?:contrase(?:ña|na)|password|passcode|pin|cvv|token|api[ -]?key|secret|"
    r"clave privada|private key|seed phrase|frase semilla)\b",
    re.IGNORECASE,
)
_LONG_NUMBER = re.compile(r"(?:\d[ -]?){13,19}")


@dataclass(frozen=True)
class MemoryRequest:
    content: str
    safe: bool


def explicit_memory_request(text: str) -> MemoryRequest | None:
    match = _MEMORY_PATTERN.match(text.strip())
    if not match:
        return None
    content = match.group(1).strip()
    safe = (
        bool(content) and not _SENSITIVE_WORDS.search(content) and not _LONG_NUMBER.search(content)
    )
    return MemoryRequest(content=content, safe=safe)


def is_safe_memory(content: str) -> bool:
    value = content.strip()
    return bool(value) and not _SENSITIVE_WORDS.search(value) and not _LONG_NUMBER.search(value)
