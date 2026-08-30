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
    "secret_key": "test-secret-key-0123456789abcdef",
    "cookie_encryption_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_DOCUMENTATION_FILES = (
    REPO_ROOT / ".env.example",
    REPO_ROOT / "README.md",
    REPO_ROOT / "OPERATIONS.md",
    REPO_ROOT / "RELEASE-CHECKLIST.md",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all settings env vars so tests are hermetic."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Keep existing tests focused on the setting under test; the required
    # cookie key is supplied explicitly by tests that exercise its absence.
    monkeypatch.setenv(
        "COOKIE_ENCRYPTION_KEY",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    )


def build(**overrides) -> Settings:
    """Construct Settings directly from keyword arguments."""
    return Settings(**{**DEFAULTS, **overrides})


class TestDefaults:
    def test_runtime_defaults(self, clean_env):
        settings = build()
        assert settings.max_concurrent == 3
        assert settings.download_speed_limit == 0
        assert settings.bubble_expire_hours == 24
        assert settings.access_token_ttl_days == 7
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

    def test_frontend_dist_path_default(self, clean_env):
        # The backend serves the built frontend at "/"; natively the build
        # lives at the repo-local frontend/dist (the container image copies
        # the build to /app/static and sets FRONTEND_DIST_PATH instead).
        assert build().frontend_dist_path == Path("frontend/dist")


class TestSessionDocumentation:
    def test_env_example_documents_seven_day_access_token_ttl(self):
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "ACCESS_TOKEN_TTL_DAYS=7" in text

    def test_auth_docs_do_not_describe_access_tokens_as_24_hour(self):
        stale_phrases = ("24-hour access token", "access tokens are 24-hour")
        for path in AUTH_DOCUMENTATION_FILES:
            text = path.read_text(encoding="utf-8").lower()
            for phrase in stale_phrases:
                assert phrase not in text, f"stale session wording in {path}"


class TestEnvOverrides:
    def test_env_vars_override_defaults(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "env-admin")
        monkeypatch.setenv("SECRET_KEY", "env-secret-key-0123456789abcdef0")
        monkeypatch.setenv("MAX_CONCURRENT", "5")
        monkeypatch.setenv("DOWNLOAD_SPEED_LIMIT", "10")
        monkeypatch.setenv("BUBBLE_EXPIRE_HOURS", "48")
        monkeypatch.setenv("CLEANUP_INTERVAL_MINUTES", "15")
        monkeypatch.setenv("STALE_DOWNLOAD_MINUTES", "5")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("TZ", "UTC")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///other.db")
        monkeypatch.setenv("VIDEO_STORAGE_PATH", "C:\\nas\\videos")
        monkeypatch.setenv("FRONTEND_DIST_PATH", "C:\\app\\static")

        settings = Settings.from_env()

        assert settings.admin_password == "env-admin"
        assert settings.secret_key == "env-secret-key-0123456789abcdef0"
        assert settings.max_concurrent == 5
        assert settings.download_speed_limit == 10
        assert settings.bubble_expire_hours == 48
        assert settings.cleanup_interval_minutes == 15
        assert settings.stale_download_minutes == 5
        assert settings.debug is True
        assert settings.tz == "UTC"
        assert settings.database_url == "sqlite:///other.db"
        assert settings.video_storage_path == Path("C:\\nas\\videos")
        assert settings.frontend_dist_path == Path("C:\\app\\static")

    def test_unset_optional_vars_fall_back_to_defaults(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
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
            "SECRET_KEY=dotenv-secret-key-0123456789abcdef\n"
            "MAX_CONCURRENT=9\n",
            encoding="utf-8",
        )
        settings = Settings.from_env(dotenv_path=dotenv_file)
        assert settings.admin_password == "dotenv-admin"
        assert settings.secret_key == "dotenv-secret-key-0123456789abcdef"
        assert settings.max_concurrent == 9

    def test_process_env_overrides_dotenv_values(self, clean_env, monkeypatch, tmp_path):
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text(
            "ADMIN_PASSWORD=dotenv-admin\n"
            "SECRET_KEY=dotenv-secret-key-0123456789abcdef\n"
            "MAX_CONCURRENT=9\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ADMIN_PASSWORD", "real-admin")
        monkeypatch.setenv("MAX_CONCURRENT", "3")
        settings = Settings.from_env(dotenv_path=dotenv_file)
        assert settings.admin_password == "real-admin"  # process env wins
        assert settings.max_concurrent == 3
        assert settings.secret_key == "dotenv-secret-key-0123456789abcdef"  # dotenv fills the gap

    def test_missing_dotenv_file_still_uses_defaults(self, clean_env, monkeypatch, tmp_path):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
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
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
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
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("CORS_ORIGINS", "   ")
        assert Settings.from_env().cors_origins == []

    def test_single_origin_env(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
        assert Settings.from_env().cors_origins == ["http://localhost:5173"]

    def test_non_json_env_value_split_on_commas(self, clean_env, monkeypatch):
        # CORS_ORIGINS is parsed as a comma-separated string, never as JSON:
        # a raw value that happens to look like (broken) JSON must still be
        # treated as a single origin, not rejected.
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("CORS_ORIGINS", "[not json")
        assert Settings.from_env().cors_origins == ["[not json"]


class TestValidation:
    @pytest.mark.parametrize("bad", [0, -1, 31, 100])
    def test_access_token_ttl_days_outside_supported_range_rejected(self, clean_env, bad):
        with pytest.raises(ValidationError):
            build(access_token_ttl_days=bad)

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

    def test_empty_frontend_dist_path_rejected(self, clean_env):
        # An empty FRONTEND_DIST_PATH would silently serve "/" from the CWD.
        with pytest.raises(ValidationError):
            build(frontend_dist_path="")

    def test_invalid_env_value_rejected(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("MAX_CONCURRENT", "not-a-number")
        with pytest.raises(ValidationError):
            Settings.from_env()

    def test_unknown_field_rejected(self, clean_env):
        # extra="forbid" catches typos in direct construction.
        with pytest.raises(ValidationError):
            Settings(admin_password="pw", secret_key="test-secret-key-0123456789abcdef", admin_pasword="typo")


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
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("TZ", "Not/AZone")
        with pytest.raises(ValidationError):
            Settings.from_env()


class TestRequiredSecrets:
    def test_both_secrets_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings()

    def test_admin_password_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(secret_key="test-secret-key-0123456789abcdef")

    def test_secret_key_required(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="pw")

    def test_empty_admin_password_rejected(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="", secret_key="test-secret-key-0123456789abcdef")

    def test_empty_secret_key_rejected(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="pw", secret_key="")

    @pytest.mark.parametrize("secret", ["sk", "change-me", "default-secret-key"])
    def test_weak_secret_key_rejected_and_not_rendered(self, clean_env, secret):
        with pytest.raises(ValidationError) as exc_info:
            Settings(admin_password="pw", secret_key=secret)
        assert secret not in str(exc_info.value)

    def test_unicode_secret_must_have_32_utf8_bytes(self, clean_env):
        with pytest.raises(ValidationError):
            Settings(admin_password="pw", secret_key="密" * 10)


class TestTrustedProxySettings:
    def test_trusted_proxy_cidrs_parse_from_env(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8, 2001:db8::/32")
        assert Settings.from_env().trusted_proxy_cidrs == ["10.0.0.0/8", "2001:db8::/32"]

    def test_invalid_trusted_proxy_cidr_rejected(self, clean_env):
        with pytest.raises(ValidationError):
            build(trusted_proxy_cidrs=["not-an-ip-network"])

    def test_mapped_trusted_proxy_cidr_is_normalized_to_ipv4(self, clean_env):
        settings = build(
            trusted_proxy_cidrs=["::ffff:10.0.0.0/120", "2001:db8::/32"]
        )
        assert settings.trusted_proxy_cidrs == ["10.0.0.0/24", "2001:db8::/32"]

    def test_from_env_requires_secrets(self, clean_env):
        with pytest.raises(ValidationError):
            Settings.from_env()


class TestSingleton:
    def test_get_settings_returns_cached_instance(self, clean_env, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
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


class TestEngineSettings:
    """Real-engine configuration: mode switches, timeouts, proxy, sources."""

    def test_engine_mode_defaults_to_stub(self, clean_env):
        settings = build()
        assert settings.parser_engine == "stub"
        assert settings.downloader_engine == "stub"

    def test_engine_mode_rejects_unknown_values(self, clean_env):
        with pytest.raises(ValidationError):
            build(parser_engine="turbo")

    def test_musicdl_sources_parse_from_comma_string(self, clean_env):
        settings = build(musicdl_sources="NeteaseMusicClient,QQMusicClient")
        assert settings.musicdl_sources == ["NeteaseMusicClient", "QQMusicClient"]

    def test_musicdl_sources_default(self, clean_env):
        assert build().musicdl_sources == [
            "MiguMusicClient", "NeteaseMusicClient", "QQMusicClient",
            "KuwoMusicClient", "QianqianMusicClient",
        ]

    def test_hot_keywords_parse_from_comma_string(self, clean_env):
        settings = build(hot_keywords="周杰伦,晴天,热歌榜")
        assert settings.hot_keywords == ["周杰伦", "晴天", "热歌榜"]

    def test_hot_keywords_default(self, clean_env):
        assert build().hot_keywords == ["周杰伦", "晴天", "热歌榜", "邓紫棋", "许嵩", "民谣"]

    def test_engine_proxy_rejects_non_http(self, clean_env):
        with pytest.raises(ValidationError):
            build(engine_proxy="ftp://x")

    def test_engine_proxy_empty_maps_to_none(self, clean_env):
        assert build(engine_proxy="").engine_proxy is None
        assert build(engine_proxy="   ").engine_proxy is None

    def test_engine_proxy_valid_url_passes_through(self, clean_env):
        settings = build(engine_proxy="http://proxy.local:3128")
        assert settings.engine_proxy == "http://proxy.local:3128"

    def test_parser_legacy_fallback_defaults_to_true(self, clean_env):
        settings = build()
        assert settings.parser_legacy_fallback is True

    def test_parser_legacy_fallback_accepts_false(self, clean_env):
        settings = build(parser_legacy_fallback=False)
        assert settings.parser_legacy_fallback is False

    def test_parser_legacy_fallback_accepts_env_strings(self, clean_env):
        settings = build(parser_legacy_fallback="false")
        assert settings.parser_legacy_fallback is False

    def test_parser_legacy_fallback_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("PARSER_LEGACY_FALLBACK", "false")
        settings = Settings.from_env()
        assert settings.parser_legacy_fallback is False

    def test_parser_legacy_fallback_rejects_invalid_value(self):
        with pytest.raises(ValidationError):
            build(parser_legacy_fallback="maybe")
