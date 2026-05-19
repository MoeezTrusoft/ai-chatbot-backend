from bookcraft.infra.config import Settings


def test_settings_defaults_are_phase_zero_safe(monkeypatch) -> None:
    # Clear APP_ENV so we read the true default, not the test-runner env var.
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings()

    assert settings.app_name == "bookcraft-chatbot"
    assert settings.app_env == "dev"
    assert settings.readiness_check_externals is False
    assert settings.trimatch_mode == "shadow"
    assert settings.funnel_signal_mode == "shadow"


def test_settings_accept_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("READINESS_CHECK_EXTERNALS", "true")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.readiness_check_externals is True
