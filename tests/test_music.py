from gwen.music import detect_spotify_request, favorite_artist, wants_history
from gwen.spotify import split_request


def test_detects_play_with_trailing_spotify_mention():
    request = detect_spotify_request("Pon una canción de Kendrick Lamar en Spotify.")
    assert request is not None
    assert (request.action, request.query) == ("play", "una canción de Kendrick Lamar")


def test_detects_transport_actions():
    assert detect_spotify_request("Ponle pausa a la canción.").action == "pause"
    assert detect_spotify_request("pausa").action == "pause"
    assert detect_spotify_request("pausa la música").action == "pause"
    assert detect_spotify_request("sáltate esta canción").action == "next"
    assert detect_spotify_request("siguiente canción en spotify").action == "next"
    assert detect_spotify_request("dale play").action == "resume"
    volume = detect_spotify_request("ponle volumen 40 en spotify")
    assert (volume.action, volume.query) == ("volume", "40")


def test_ignores_unrelated_messages():
    assert detect_spotify_request("¿cómo va el clima?") is None
    assert detect_spotify_request("pon ") is None


def test_ignores_non_music_uses_of_poner():
    assert detect_spotify_request("ponle atención a esto") is None
    assert detect_spotify_request("pon una alarma a las 7") is None
    assert detect_spotify_request("pon un recordatorio") is None
    assert detect_spotify_request("pon la mesa") is None


def test_keeps_titles_without_music_words():
    request = detect_spotify_request("pon persiana americana de soda stereo")
    assert request is not None and request.query == "persiana americana de soda stereo"


def test_favorites_need_stored_memory():
    request = detect_spotify_request("pon algo de mis artistas favoritos")
    assert request is not None and request.needs_favorite
    assert favorite_artist([]) == ""
    assert favorite_artist(["artistas: Zoé, Caifanes"]) in {"Zoé", "Caifanes"}


def test_history_detection():
    assert wants_history("¿qué escuché en spotify?")
    assert not wants_history("pon música")


def test_split_request_separates_title_and_artist():
    assert split_request("persiana americana de soda stereo") == ("persiana americana", "soda stereo")
    assert split_request("una cancion de zoe") == ("", "zoe")
    assert split_request("persiana americana") == ("persiana americana", "")
