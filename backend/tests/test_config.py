"""Tests for the typed application settings module.

Covers: defaults, environment-var overrides, CORS list parsing, validation
failures, and required-secret handling.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.infrastructure.config import Settings, get_settings

# Every environment variable the settings object reads. Tests delete all of
# them first so results never depend on ambient machine state. Derived from
# the model so the list cannot drift from the field contract.
ENV_NAMES = tuple(name.upper() for name in Settings.model_fields)

DEFAULTS = {
    "admin_password": "pw",
    "secret_key": "sk",
}


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all settings env vars so tests are hermetic."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def build(**overrides) -> Settings:
    """Construct Settings directly from keyword arguments."""
    return Settings(**{**DEFAULTS, **overrides})


class TestDefaults:
    def test_runtime_defaults(self, clean_env):
        settings = build()
        assert settings.max_concurrent == 3
        assert settings.download_speed_limit == 0
        assert settings.bubble_expire_hours == 24
        assert settings.worker_poll_interval == 1.0
        assert settings.cleanup_interval_minutes == 60
        assert settings.stale_download_minutes == 60
        assert settings.debug is False
        assert settings.tz == "Asia/Shanghai"

    def test_cors_origins_empty_by_default(self, clean_env):
        assert build().cors_origins == []

    def test_storage_root_defaults(self, clean_env):
        settings = build()
        assert settings.video_storage_path == Path("data/pond/video")
        assert settings.image_storage_path == Path("data/pond/image")
        assert settings.music_storage_path == Path("data/pond/music")
        assert settings.temp_video_path == Path("data/bubble/video")
        assert settings.temp_image_path == Path("data/bubble/image")
        assert settings.temp_music_path == Path("data/bubble/music")

    def test_database_url_default(self, clean_env):
        assert build().database_url == "sqlite:///./data/db/koifetch.db"


class TestEnvOverrides:
    def test_env_vars_override_defaults(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "env-admin")
        monkeypatch.setenv("SECRET_KEY", "env-secret")
        monkeypatch.setenv("MAX_CONCURRENT", "5")
        monkeypatch.setenv("DOWNLOAD_SPEED_LIMIT", "10")
        monkeypatch.setenv("BUBBLE_EXPIRE_HOURS", "48")
        monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", "15")
        monkeypatch.setenv("STALE_DOWNLOAD_MINUTES", "5")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("TZ", "UTC")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///other.db")
        monkeypatch.setenv("VIDEO_STORAGE_PATH", "C:\\nas\\videos")

        settings = Settings.from_env()

        assert settings.admin_password == "env-admin"
        assert settings.secret_key == "env-secret"
        assert settings.max_concurrent == 5
        assert settings.download_speed_limit == 10
        assert settings.bubble_expire_hours == 48
        assert settings.cleanup_interval_minutes == 15
        assert settings.stale_download_minutes == 5
        assert settings.debug is True
        assert settings.tz == "UTC"
        assert settings.database_url == "sqlite:///other.db"
        assert settings.video_storage_path == Path("C:\\nas\\videos")

    def test_unset_optional_vars_fall_back_to_defaults(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        settings = Settings.from_env()
        assert settings.max_concurrent == 3
        assert settings.bubble_expire_hours == 24
        assert settings.cors_origins == []


class TestDotenvLoading:
    @pytest.fixture(autouse=True)
    def no_dotenv_file(self):
        """Opt out of the no-op above: these tests exercise real ``.env`` loading."""
        return None

    def test_dotenv_values_picked_up_when_env_unset(self, clean_env, tmp_path):
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text(
            "ADMIN_PASSWORD=dotenv-admin\n"
            "SECRET_KEY=dotenv-secret\n"
            "MAX_CONCURRENT=9\n",
            encoding="utf-8",
        )
        settings = Settings.from_env(dotenv_path=dotenv_file)
        assert settings.admin_password == "dotenv-admin"
        assert settings.secret_key == "dotenv-secret"
        assert settings.max_concurrent == 9

    def test_process_env_overrides_dotenv_values(self, clean_env, monkeypatch, tmp_path):
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text(
            "ADMIN_PASSWORD=dotenv-admin\n"
            "SECRET_KEY=dotenv-secret\n"
            "MAX_CONCURRENT=9\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ADMIN_PASSWORD", "real-admin")
        monkeypatch.setenv("MAX_CONCURRENT", "3")
        settings = Settings.from_env(dotenv_path=dotenv_file)
        assert settings.admin_password == "real-admin"  # process env wins
        assert settings.max_concurrent == 3
        assert settings.secret_key == "dotenv-secret"  # dotenv fills the gap

    def test_missing_dotenv_file_still_uses_defaults(self, clean_env, monkeypatch, tmp_path):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        settings = Settings.from_env(dotenv_path=tmp_path / ".env")  # does not exist
        assert settings.max_concurrent == 3
        assert settings.bubble_expire_hours == 24
        assert settings.cors_origins == []

    def test_missing_dotenv_file_without_secrets_still_raises(self, clean_env, tmp_path):
        with pytest.raises(ValidationError):
            Settings.from_env(dotenv_path=tmp_path / ".env")


class TestCorsParsing:
    def test_comma_separated_env_parsed_into_list(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv(
            "CORS_ORIGINS",
            "http://localhost:5173, http://localhost:8000 ,https://example.com",
        )
        settings = Settings.from_env()
        assert settings.cors_origins == [
            "http://localhost:5173",
            "http://localhost:8000",
            "https://example.com",
        ]

    def test_whitespace_only_env_yields_empty_list(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv("CORS_ORIGINS", "   ")
        assert Settings.from_env().cors_origins == []

    def test_single_origin_env(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
        assert Settings.from_env().cors_origins == ["http://localhost:5173"]

    def test_non_json_env_value_split_on_commas(self, clean_env, monkeypatch):
        # CORS_ORIGINS is parsed as a comma-separated string, never as JSON:
        # a raw value that happens to look like (broken) JSON must still be
        # treated as a single origin, not rejected.
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv("CORS_ORIGINS", "[not json")
        assert Settings.from_env().cors_origins == ["[not json"]


class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1, -10])
    def test_max_concurrent_below_minimum_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(max_concurrent=bad)

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_bubble_expire_hours_below_minimum_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(bubble_expire_hours=bad)

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_cleanup_interval_minutes_below_minimum_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(cleanup_interval_minutes=bad)

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_stale_download_minutes_below_minimum_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(stale_download_minutes=bad)

    @pytest.mark.parametrize("bad", [-1, -100])
    def test_download_speed_limit_negative_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(download_speed_limit=bad)

    @pytest.mark.parametrize(
        "field",
        [
            "video_storage_path",
            "image_storage_path",
            "music_storage_path",
            "temp_video_path",
            "temp_image_path",
            "temp_music_path",
        ],
    )
    def test_empty_storage_path_rejected(self, clean_env, field):
        with pytest.raises(ValidationError):
            build(**{field: ""})

    def test_invalid_env_value_rejected(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv("MAX_CONCURRENT", "not-a-number")
        with pytest.raises(ValidationError):
            Settings.from_env()

    def test_unknown_field_rejected(self, clean_env):
        # extra="forbid" catches typos in direct construction.
        with pytest.raises(ValidationError):
            Settings(admin_password="pw", secret_key="sk", admin_pasword="typo")


class TestTimezone:
    @pytest.mark.parametrize("tz", ["Asia/Shanghai", "UTC", "America/New_York"])
    def test_valid_timezone_accepted(self, clean_env, tz):
        settings = build(tz=tz)
        assert settings.tz == tz

    @pytest.mark.parametrize("tz", ["Asia/Shnghai", "Not/AZone", "Shanghai"])
    def test_invalid_timezone_rejected(self, clean_env, tz):
        with pytest.raises(ValidationError):
            build(tz=tz)

    def test_invalid_timezone_env_rejected(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        monkeypatch.setenv("TZ", "Not/AZone")
        with pytest.raises(ValidationError):
            Settings.from_env()


class TestRequiredSecrets:
    def test_both_secrets_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings()

    def test_admin_password_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(secret_key="sk")

    def test_secret_key_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="pw")

    def test_empty_admin_password_rejected(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="", secret_key="sk")

    def test_empty_secret_key_rejected(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="pw", secret_key="")

    def test_from_env_requires_secrets(self, clean_env):
        with pytest.raises(ValidationError):
            Settings.from_env()


class TestSingleton:
    def test_get_settings_returns_cached_instance(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "sk")
        get_settings.cache_clear()
        try:
            first = get_settings()
            second = get_settings()
            assert first is second
        finally:
            get_settings.cache_clear()

    def test_get_settings_requires_secrets(self, clean_env):
        get_settings.cache_clear()
        try:
            with pytest.raises(ValidationError):
                get_settings()
        finally:
            get_settings.cache_clear()
