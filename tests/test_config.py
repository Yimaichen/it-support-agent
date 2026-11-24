import pytest

from src.config import PROJECT_ROOT, Settings


def test_defaults_are_offline_and_paths_are_project_relative():
    config = Settings.from_env({})
    assert not config.model_configured
    assert config.model == "deepseek-flash"
    assert config.database_path == PROJECT_ROOT / "runtime/support.sqlite3"


def test_secret_is_not_in_repr():
    assert "do-not-log-this" not in repr(Settings(api_key="do-not-log-this"))


@pytest.mark.parametrize(
    "env",
    [
        {"MAX_TOOL_CALLS": "0"},
        {"TURN_TIMEOUT_SECONDS": "-1"},
        {"MODEL_BASE_URL": "file:///etc/passwd"},
        {"MAX_ADDITIONAL_RETRIES": "-1"},
        {"MODEL_NAME": ""},
        {"MODEL_BASE_URL": "https://user:secret@example.com"},
        {"TURN_TIMEOUT_SECONDS": "nan"},
        {"TURN_TIMEOUT_SECONDS": "inf"},
        {"REQUEST_TIMEOUT_SECONDS": "nan"},
        {"REQUEST_TIMEOUT_SECONDS": "inf"},
    ],
)
def test_bad_config_rejected(env):
    with pytest.raises(ValueError):
        Settings.from_env(env)
