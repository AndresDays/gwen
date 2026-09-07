import re
from dataclasses import dataclass

_MEMORY_PATTERN = re.compile(
    r"^(?:por favor,?\s*)?(?:recuerda|recordá|remember)(?:\s+que)?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_AUTOMATIC_PREFERENCE = re.compile(
    r"^(?:yo\s+)?(?:prefiero|me gusta|no me gusta|háblame)(?:\s+que)?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MemoryRequest:
    content: str


def explicit_memory_request(text: str) -> MemoryRequest | None:
    match = _MEMORY_PATTERN.match(text.strip())
    if not match:
        return None
    content = match.group(1).strip()
    return MemoryRequest(content=content)


def is_safe_memory(content: str) -> bool:
    return bool(content.strip())


def automatic_preference_request(text: str) -> str | None:
    match = _AUTOMATIC_PREFERENCE.match(text.strip())
    if not match:
        return None
    content = text.strip()
    return content or None
