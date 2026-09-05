"""OAuth local para la cuenta privada de Spotify de Gwen."""

import base64
import json
import re
import secrets
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

import httpx

SCOPES = (
    "user-read-private user-read-email user-read-recently-played user-top-read "
    "user-library-read playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public user-read-playback-state "
    "user-modify-playback-state"
)

# Frases con las que se pide "algo" de un artista, sin nombrar una canción.
GENERIC_TITLES = {
    "algo", "una cancion", "una rola", "un tema", "una cancion cualquiera",
    "musica", "algo de musica", "cualquier cosa", "lo que sea", "una canci n",
}


def normalize(text: str) -> str:
    """Minúsculas sin acentos ni signos, para comparar nombres."""
    stripped = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(
        char if char.isalnum() else " " for char in stripped if not unicodedata.combining(char)
    ).split())


def split_request(query: str) -> tuple[str, str]:
    """Separa "persiana americana de soda stereo" en (canción, artista)."""
    text = " ".join(query.strip().split())
    match = re.search(r"^(.*)\s+(?:de|del|by)\s+(.+)$", text, flags=re.I)
    if not match:
        return text, ""
    title, artist = match.group(1).strip(), match.group(2).strip()
    title = re.sub(
        r"^(?:la|el|una|un)?\s*(?:cancion|canción|rola|tema)\s*(?:de\s+)?", "", title, flags=re.I
    ).strip()
    if normalize(title) in GENERIC_TITLES:
        title = ""
    return title, artist


class SpotifyOAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, path: Path) -> None:
        self.client_id, self.client_secret, self.redirect_uri, self.path = (
            client_id,
            client_secret,
            redirect_uri,
            path,
        )
        self.state: str | None = None

    def authorization_url(self) -> str:
        self.state = secrets.token_urlsafe(32)
        return "https://accounts.spotify.com/authorize?" + urlencode(
            {"response_type": "code", "client_id": self.client_id, "scope": SCOPES,
             "redirect_uri": self.redirect_uri, "state": self.state}
        )

    def connected(self) -> bool:
        return self.path.is_file()

    def _store(self, data: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path)

    async def complete(self, code: str, state: str) -> None:
        if not self.state or not secrets.compare_digest(state, self.state):
            raise ValueError("La autorización de Spotify no es válida.")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Authorization": f"Basic {basic}"},
            )
            response.raise_for_status()
        self._store(response.json())
        self.state = None

    def _token(self) -> str:
        try:
            token = json.loads(self.path.read_text(encoding="utf-8"))["access_token"]
        except (OSError, ValueError, KeyError) as error:
            raise ValueError("Spotify no está conectado.") from error
        return token

    async def _refresh(self) -> str:
        """Renueva el access token; sin esto Gwen deja de responder tras una hora."""
        data = json.loads(self.path.read_text(encoding="utf-8"))
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise ValueError("La autorización de Spotify venció; vuelve a conectarla.")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                headers={"Authorization": f"Basic {basic}"},
            )
            response.raise_for_status()
        data.update(response.json())
        data.setdefault("refresh_token", refresh_token)
        self._store(data)
        return data["access_token"]

    async def _authorized(self, call) -> httpx.Response:
        """Ejecuta la llamada y la repite una vez con el token renovado si expiró."""
        token = self._token()
        for attempt in range(2):
            try:
                return await call({"Authorization": f"Bearer {token}"})
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 401 or attempt:
                    raise
                token = await self._refresh()
        raise ValueError("La autorización de Spotify venció; vuelve a conectarla.")

    async def recently_played(self) -> list[str]:
        async def call(headers: dict[str, str]) -> httpx.Response:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://api.spotify.com/v1/me/player/recently-played?limit=10",
                    headers=headers,
                )
                response.raise_for_status()
                return response

        response = await self._authorized(call)
        tracks: list[str] = []
        for item in response.json().get("items", []):
            track = item["track"]
            artists = ", ".join(artist["name"] for artist in track["artists"])
            tracks.append(f"{track['name']} — {artists}")
        return tracks

    async def _search(
        self, client: httpx.AsyncClient, headers: dict[str, str], term: str
    ) -> list[dict]:
        found = await client.get(
            "https://api.spotify.com/v1/search",
            params={"q": term, "type": "track", "limit": 10},
            headers=headers,
        )
        found.raise_for_status()
        return found.json().get("tracks", {}).get("items", [])

    async def find_track(
        self, client: httpx.AsyncClient, headers: dict[str, str], query: str
    ) -> dict:
        """Busca una canción a partir de una frase como "persiana americana de soda stereo"."""
        title, artist = split_request(query)
        terms: list[str] = []
        if title and artist:
            terms.append(f'track:"{title}" artist:"{artist}"')
            terms.append(f"{title} {artist}")
        if artist:
            terms.append(f'artist:"{artist}"')
        if title:
            terms.append(title)
        for term in terms or [query]:
            matches = await self._search(client, headers, term)
            if not matches:
                continue
            if artist:
                expected = normalize(artist)
                preferred = next(
                    (
                        item
                        for item in matches
                        if any(expected in normalize(name["name"]) for name in item["artists"])
                    ),
                    None,
                )
                if preferred is not None:
                    return preferred
            elif title:
                expected = normalize(title)
                preferred = next(
                    (item for item in matches if expected in normalize(item["name"])), None
                )
                if preferred is not None:
                    return preferred
            return matches[0]
        raise ValueError(f"Spotify no encontró nada para: {query}")

    async def control(self, action: str, query: str = "") -> str:
        track: dict | None = None

        async def call(headers: dict[str, str]) -> httpx.Response:
            nonlocal track
            async with httpx.AsyncClient(timeout=20) as client:
                if action == "pause":
                    response = await client.put(
                        "https://api.spotify.com/v1/me/player/pause", headers=headers
                    )
                elif action == "resume":
                    response = await client.put(
                        "https://api.spotify.com/v1/me/player/play", headers=headers
                    )
                elif action == "next":
                    response = await client.post(
                        "https://api.spotify.com/v1/me/player/next", headers=headers
                    )
                elif action == "volume":
                    response = await client.put(
                        "https://api.spotify.com/v1/me/player/volume",
                        params={"volume_percent": query}, headers=headers,
                    )
                else:
                    track = await self.find_track(client, headers, query)
                    response = await client.put(
                        "https://api.spotify.com/v1/me/player/play",
                        json={"uris": [track["uri"]]},
                        headers=headers,
                    )
                response.raise_for_status()
                return response

        await self._authorized(call)
        if track is not None:
            artists = ", ".join(artist["name"] for artist in track["artists"])
            return f"{track['name']} de {artists}"
        return ""
