from upm_central.config import CentralDatabaseSettings


def settings(**overrides: object) -> CentralDatabaseSettings:
    return CentralDatabaseSettings(
        database_url="postgresql+psycopg://user:password@db/upm",
        admin_token="a" * 32,
        credential_issuer_key="b" * 32,
        **overrides,
    )


def test_destructive_testing_tools_are_disabled_by_default() -> None:
    assert settings().enable_destructive_test_tools is False


def test_destructive_testing_tools_can_be_explicitly_enabled() -> None:
    assert settings(enable_destructive_test_tools=True).enable_destructive_test_tools is True
