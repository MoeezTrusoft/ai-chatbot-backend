from bookcraft.components.language_guard import LanguageGuard


def test_short_english_messages_default_to_english() -> None:
    guard = LanguageGuard()

    assert guard.detect("hi").is_english
    assert guard.detect("hello").is_english
    assert guard.detect("price?").is_english


def test_mixed_service_message_is_handled_generously() -> None:
    decision = LanguageGuard().detect("Hola, I need editing for my book")

    assert decision.is_english
    assert decision.language == "en"


def test_clear_non_english_paragraph_gets_redirect() -> None:
    decision = LanguageGuard().detect(
        "Hola necesito ayuda con mi manuscrito y quiero saber que servicios ofrecen"
    )

    assert not decision.is_english
    assert decision.redirect_message is not None


def test_lingua_failure_defaults_to_english(monkeypatch) -> None:
    guard = LanguageGuard()

    def fail(_: str, __: float):
        raise RuntimeError("lingua unavailable")

    monkeypatch.setattr(LanguageGuard, "_detect_with_lingua", fail)

    decision = guard.detect("zzzzzzzzzzzzzzzzzzzz")

    assert decision.is_english
    assert decision.source == "failure_default"
