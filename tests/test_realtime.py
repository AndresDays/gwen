from gwen.realtime import split_speech


def test_split_speech_releases_complete_sentences() -> None:
    ready, pending = split_speech("Hola. Esta parte sigue")
    assert ready == ["Hola."]
    assert pending == "Esta parte sigue"


def test_split_speech_flushes_final_fragment() -> None:
    ready, pending = split_speech("Una respuesta breve", force=True)
    assert ready == ["Una respuesta breve"]
    assert pending == ""


def test_split_speech_limits_first_audio_fragment() -> None:
    text = "palabra " * 20
    ready, pending = split_speech(text)
    assert ready
    assert len(ready[0]) <= 110
    assert pending
