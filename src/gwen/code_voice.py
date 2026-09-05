import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_IDS = ("california", "gwen")
CONTEXT_TTL_SECONDS = 900

EDITING_VERBS = (
    r"\b(edita|modifica|cambia|corrige|corrigelas|corrigelos|corrigela|corrigelo|"
    r"implementa|programa|arregla|arreglalas|arreglalos|arreglala|arreglalo|"
    r"agrega|anade|actualiza|actualizar|bump|haz)\b"
)
READING_VERBS = r"\b(ver|revisa|revisar|mira|analiza|analizar|inspecciona|lee)\b"
CODE_VERBS = (
    r"\b(edita|modifica|cambia|corrige|corrigelas|corrigelos|corrigela|corrigelo|"
    r"implementa|programa|arregla|arreglalas|arreglalos|arreglala|arreglalo|"
    r"agrega|anade|actualiza|actualizar|bump|haz|trabaja|ver|revisa|revisar|mira|analiza|analizar|inspecciona|lee)\b"
)

PROJECT_PATTERNS = {
    "california": (
        r"\b(proyecto|codigo|repositorio)\s+(de\s+)?california\b",
        r"\b(trabaja|metete|entra)\s+(en|a)\s+(el\s+proyecto\s+)?california\b",
    ),
    "gwen": (
        r"\b(proyecto|codigo|repositorio)\s+(de\s+)?gwen\b",
        r"\b(trabaja|metete|entra)\s+(en|a)\s+(el\s+proyecto\s+)?gwen\b",
    ),
}


@dataclass
class PendingCodeTask:
    workspace_id: str
    task: str
    plan: str


@dataclass
class CodeContext:
    """Último proyecto tocado, para que "corrige eso" siga la conversación."""

    workspace_id: str
    updated_at: float
    last_failures: tuple[str, ...] = ()

    def is_fresh(self, now: float | None = None) -> bool:
        elapsed = (now if now is not None else time.time()) - self.updated_at
        return 0 <= elapsed < CONTEXT_TTL_SECONDS


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    return "".join(character for character in value if not unicodedata.combining(character))


def named_workspace(text: str) -> str | None:
    value = normalized(text)
    for workspace_id, patterns in PROJECT_PATTERNS.items():
        if any(re.search(pattern, value) for pattern in patterns):
            return workspace_id
    return None


def detect_code_request(text: str, context: CodeContext | None = None) -> tuple[str, str] | None:
    value = normalized(text)
    if _commit_is_negated(value):
        return None
    # Un commit es una acción de código por sí sola. Si el proyecto no se repite,
    # usamos únicamente el contexto reciente del trabajador, nunca el LLM.
    if code_commit_requested(text):
        workspace_id = named_workspace(text)
        if workspace_id is not None:
            return workspace_id, text.strip()
        if context is not None and context.is_fresh():
            return context.workspace_id, text.strip()
        return None
    if not re.search(CODE_VERBS, value):
        return None
    workspace_id = named_workspace(text)
    if workspace_id is not None:
        return workspace_id, text.strip()
    if context is not None and context.is_fresh():
        return context.workspace_id, text.strip()
    return None


def detect_validation_request(text: str, context: CodeContext | None = None) -> str | None:
    value = normalized(text)
    if not re.search(r"\b(pruebas?|tests?|validaciones?)\b", value):
        return None
    if re.search(EDITING_VERBS, value):
        # "checa las pruebas y corrigelas" es una edición, no una validación pasiva.
        return None
    if not re.search(
        r"\b(falla|fallan|fallaron|fallando|checa|revisa|revisar|cuales|resultados?)\b", value
    ):
        return None
    for workspace_id in WORKSPACE_IDS:
        if re.search(rf"\b{workspace_id}\b", value):
            return workspace_id
    if context is not None and context.is_fresh():
        return context.workspace_id
    return None


def detect_adopt_request(text: str, context: CodeContext | None = None) -> str | None:
    """«adopta los cambios de california» — tomar el árbol actual como punto de partida."""
    value = normalized(text)
    if not re.search(r"\b(adopta|adoptar|toma|acepta)\b", value):
        return None
    if not re.search(r"\bcambios?\b", value):
        return None
    for workspace_id in WORKSPACE_IDS:
        if re.search(rf"\b{workspace_id}\b", value):
            return workspace_id
    if context is not None and context.is_fresh():
        return context.workspace_id
    return None


def code_task_needs_diagnosis(text: str) -> bool:
    """«corrige los errores de test y lint» exige correr los checks primero."""
    value = normalized(text)
    if not re.search(EDITING_VERBS, value):
        return False
    return bool(re.search(r"\b(pruebas?|tests?|lint|linter|errores?|fallas?|warnings?)\b", value))


def code_request_is_read_only(text: str) -> bool:
    value = normalized(text)
    return bool(re.search(READING_VERBS, value)) and not re.search(EDITING_VERBS, value)


def code_followup_task(text: str, context: CodeContext | None) -> str:
    """Adjunta los fallos previos para que Claude Code sepa qué arreglar."""
    task = text.strip()
    if context is None or not context.last_failures or not context.is_fresh():
        return task
    detail = "\n".join(f"- {failure}" for failure in context.last_failures)
    return (
        f"{task}\n\nContexto: en la última validación de este proyecto fallaron "
        f"estas comprobaciones:\n{detail}"
    )


def failed_checks(result: dict[str, object]) -> tuple[str, ...]:
    checks = result.get("checks") or []
    return tuple(str(check) for check in checks if str(check).startswith("FALLÓ:"))


def code_commit_requested(text: str) -> bool:
    value = normalized(text)
    # Ante cualquier negación explícita, la opción segura es no confirmar.
    # Esto cubre "no hagas commit", "sin commit" y variantes con cortesía.
    if _commit_is_negated(value):
        return False
    return bool(re.search(r"\b(commit|confirma los cambios|guarda el commit)\b", value))


def _commit_is_negated(value: str) -> bool:
    return bool(
        re.search(r"\b(?:no|sin)\b.{0,48}\b(?:commit|confirma(?:r)? los cambios)\b", value)
    )


def code_confirmation(text: str) -> tuple[bool, bool] | None:
    value = normalized(text)
    if re.search(r"\b(no|cancela|cancelar|detente)\b", value):
        return False, False
    confirmed = bool(re.search(r"\b(si|hazlo|ejecuta|adelante|confirmo)\b", value))
    if not confirmed:
        return None
    commit = bool(re.search(r"\bcommit|confirma los cambios|guarda el commit\b", value))
    return True, commit


class CodeContextStore:
    """Recuerda el último proyecto de código tocado, compartido entre chat y voz."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._context = self._load()

    def _load(self) -> CodeContext | None:
        if self.path is None:
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return CodeContext(
                workspace_id=str(value["workspace_id"]),
                updated_at=float(value["updated_at"]),
                last_failures=tuple(str(item) for item in value.get("last_failures", [])),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self) -> None:
        if self.path is None or self._context is None:
            return
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "workspace_id": self._context.workspace_id,
                    "updated_at": self._context.updated_at,
                    "last_failures": self._context.last_failures,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def current(self) -> CodeContext | None:
        if self._context is not None and self._context.is_fresh():
            return self._context
        self._context = None
        return None

    def remember(self, workspace_id: str, failures: tuple[str, ...] = ()) -> None:
        self._context = CodeContext(
            workspace_id=workspace_id, updated_at=time.time(), last_failures=failures
        )
        self._save()

    def clear(self) -> None:
        self._context = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
