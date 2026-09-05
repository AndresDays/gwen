"""Detección de peticiones de Spotify, compartida entre el chat de texto y la voz."""

import random
import re
from dataclasses import dataclass

import httpx

TRIGGERS = ("pon ", "ponle ", "ponme ", "reproduce ", "reproduceme ", "dale play", "play ")

# Palabras que confirman que se habla de música y no de otra cosa.
MUSIC_WORDS = (
    "cancion", "canción", "musica", "música", "rola", "tema", "disco",
    "album", "álbum", "playlist", "spotify", "sonando",
)

ANSWERS = {
    "pause": "Listo, la pausé.",
    "resume": "Listo, ya sigue sonando.",
    "next": "Listo, pasé a la siguiente.",
    "volume": "Listo, ajusté el volumen.",
}

NO_FAVORITES = "No tengo artistas favoritos guardados todavía. Dime cuáles te gustan."


@dataclass(frozen=True)
class SpotifyRequest:
    action: str
    query: str = ""
    needs_favorite: bool = False


def detect_spotify_request(message: str) -> SpotifyRequest | None:
    """Reconoce "pon algo de X", "pausa", "siguiente", "volumen 40"."""
    text = " ".join(message.strip().split())
    lower = text.lower()
    # "pausa" o "siguiente" bastan si hablamos de música o si la orden es escueta.
    about_music = any(word in lower for word in MUSIC_WORDS) or len(lower.split()) <= 2
    pausing = bool(re.search(r"\bpausa\w*\b|\bpausar\b", lower)) and about_music
    skipping = bool(re.search(r"\bsiguiente\b|\bsáltate\b|\bsaltate\b", lower)) and about_music
    resuming = bool(re.search(r"\breanuda\w*\b", lower)) or lower.startswith(("dale play", "play "))
    if not (
        "spotify" in lower
        or lower.startswith(TRIGGERS)
        or pausing
        or skipping
        or resuming
    ):
        return None
    if pausing:
        return SpotifyRequest("pause")
    if skipping:
        return SpotifyRequest("next")
    if "volumen" in lower:
        digits = "".join(character for character in text if character.isdigit())
        return SpotifyRequest("volume", digits or "50")
    if resuming:
        return SpotifyRequest("resume")
    if lower.startswith(("pon ", "ponle ", "ponme ", "reproduce ", "reproduceme ")):
        query = text.split(" ", 1)[1]
        # "en Spotify" al final es contexto, no parte del nombre de la canción.
        query = re.sub(r"[\s,]*\ben\s+spotify\b[\s.!?]*$", "", query, flags=re.I).strip(" .!?¿¡")
        if "artistas favoritos" in lower:
            return SpotifyRequest("play", "", needs_favorite=True)
        if not query:
            return None
        return SpotifyRequest("play", query)
    return None


def wants_history(message: str) -> bool:
    lower = message.lower()
    return "spotify" in lower and any(
        word in lower for word in ("escuche", "escuché", "historial", "reciente")
    )


def favorite_artist(contents: list[str]) -> str:
    """Elige al azar un artista de la memoria más reciente que liste favoritos."""
    favorite = next(
        (
            content
            for content in reversed(contents)
            if re.search(r"\bartistas?\s*:", content, re.IGNORECASE)
        ),
        "",
    )
    if not favorite:
        return ""
    match = re.search(r"\bartistas?\s*:\s*(.+)", favorite, re.IGNORECASE)
    listed = (
        match.group(1)
        if match
        else re.split(r"\b(?:me\s+)?gust(?:a|an)|\bfavorit\w*\s*(?:son)?", favorite)[-1]
    )
    candidates = [candidate.strip(" .:") for candidate in re.split(r"[,;|]|\by\b", listed)]
    candidates = [candidate for candidate in candidates if len(candidate) > 2]
    if not candidates:
        return ""
    return random.choice(candidates)


def spotify_answer(action: str, result: str) -> str:
    return f"Listo, puse {result}." if action == "play" else ANSWERS[action]


def spotify_failure(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return {
            404: "No encontré un dispositivo activo de Spotify.",
            403: "Spotify no permitió esa acción en este dispositivo o plan.",
            401: "La autorización de Spotify venció; vuelve a conectarla.",
        }.get(error.response.status_code, "Spotify no pudo completar esa acción ahora.")
    return str(error)
