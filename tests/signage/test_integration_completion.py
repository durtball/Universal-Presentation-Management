from pathlib import Path

from upm_signage.config import Settings
from upm_signage.worker import _cipher, _secret


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://user:password@postgres/signage",
        enrollment_secret_file=str(tmp_path / "enrollment-secret"),
    )


def test_enrollment_credential_is_encrypted_and_survives_restart(tmp_path: Path):
    secret = "site-owned-enrollment-secret-with-sufficient-entropy"
    (tmp_path / "enrollment-secret").write_text(secret)
    configured = settings(tmp_path)
    encrypted = _cipher(_secret(configured)).encrypt(b"projection-credential")
    assert b"projection-credential" not in encrypted
    restarted = settings(tmp_path)
    assert _cipher(_secret(restarted)).decrypt(encrypted) == b"projection-credential"


def test_missing_or_short_enrollment_secret_is_rejected(tmp_path: Path):
    configured = settings(tmp_path)
    assert _secret(configured) is None
    (tmp_path / "enrollment-secret").write_text("short")
    assert _secret(configured) is None


def test_compose_automates_enrollment_and_multi_event_sync():
    site = Path("docker-compose.site.yml").read_text()
    signage = Path("docker-compose.signage.yml").read_text()
    assert "signage-enrollment-bootstrap" in site
    assert "signage-enrollment-bootstrap" in signage
    assert (
        "UPM_SIGNAGE_SITE_URL: '${UPM_SIGNAGE_SITE_URL:-http://host.docker.internal:9080}'"
        in signage
    )
    assert "UPM_SIGNAGE_EVENT_ID: '${UPM_SIGNAGE_EVENT_ID:-}'" in signage
    assert "UPM_SIGNAGE_SITE_CREDENTIAL:?" not in signage
    assert "UPM_SITE_SIGNAGE_PROJECTION_TOKEN:?" not in site


def test_discovery_and_windows_first_run_do_not_depend_on_wsdd():
    responder = Path("signage/python/src/upm_signage/discovery.py").read_text()
    client = Path("clients/windows/UPM.Signage/SignageDiscoveryService.cs").read_text()
    window = Path("clients/windows/UPM.Signage/MainWindow.xaml").read_text()
    assert "UPM_SIGNAGE_DISCOVERY_V1" in responder
    assert "UPM_SIGNAGE_DISCOVERY_V1" in client
    assert "Finding UPM Signage" in window
    assert "Advanced / Manual Connection" in window
    assert "wsdd" not in responder.casefold()
