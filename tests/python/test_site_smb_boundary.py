"""Site-local SMB feature gating and WAN transport boundary regressions."""

from pathlib import Path
from types import SimpleNamespace

from upm_shared.enums import JobStatus
from upm_site.config import SiteSettings
from upm_site.worker import LOCAL_SMB_JOB_TYPES, disable_local_smb_jobs

ROOT = Path(__file__).resolve().parents[2]


def test_site_smb_is_disabled_by_default_and_explicitly_enableable(monkeypatch) -> None:
    monkeypatch.delenv("UPM_SITE_SMB_ENABLED", raising=False)
    assert SiteSettings(database_url="postgresql+psycopg://u:p@db/site").smb_enabled is False
    monkeypatch.setenv("UPM_SITE_SMB_ENABLED", "true")
    assert SiteSettings(database_url="postgresql+psycopg://u:p@db/site").smb_enabled is True


def test_disabling_site_smb_terminalizes_existing_local_jobs() -> None:
    jobs = [
        SimpleNamespace(
            status=JobStatus.RETRY_WAIT,
            progress=0,
            completed_at=None,
            claimed_by_worker_id="old-worker",
            lease_expires_at=object(),
            heartbeat_at=object(),
            error_code="smb_unavailable",
            last_error="connection refused",
            error_metadata=None,
        )
        for _job_type in LOCAL_SMB_JOB_TYPES
    ]

    class Session:
        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: jobs)

    assert disable_local_smb_jobs(Session()) == len(LOCAL_SMB_JOB_TYPES)
    assert all(job.status is JobStatus.SUCCEEDED for job in jobs)
    assert all(job.progress == 100 for job in jobs)
    assert all(job.claimed_by_worker_id is None for job in jobs)
    assert all(job.error_metadata == {"disabled_feature": "site_local_smb"} for job in jobs)


def test_central_site_media_transports_are_http_only_and_smb_free() -> None:
    pull = (ROOT / "site/python/src/upm_site/media/transfer.py").read_text()
    push = (ROOT / "site/python/src/upm_site/media/replication.py").read_text()
    sync = (ROOT / "site/python/src/upm_site/sync_transport.py").read_text()
    central_receive = (
        ROOT / "central/python/src/upm_central/presentation_media_api.py"
    ).read_text()
    central_finalize = (ROOT / "central/python/src/upm_central/media_replication.py").read_text()

    for source in (pull, push, sync, central_receive, central_finalize):
        assert "SmbControlClient" not in source
        assert "/storage/smb/" not in source
        assert "smb_incoming" not in source
        assert "smb_presentations" not in source
    assert "client.stream(" in pull
    assert "storage.append_staging(" in pull
    assert "storage.commit(" in pull
    assert "client.put(" in push
    assert "storage.read_object(" in push


def test_site_compose_keeps_smb_optional_and_disabled_for_http_workers() -> None:
    compose = (ROOT / "docker-compose.site.yml").read_text()
    assert 'profiles: ["smb"]' in compose
    assert compose.count("UPM_SITE_SMB_ENABLED: ${UPM_SITE_SMB_ENABLED:-false}") == 3
    assert "UPM_SITE_CENTRAL_URL: ${UPM_SITE_CENTRAL_URL:-http://upm-central}" in compose
    assert "UPM_SITE_SMB_CONTROL_TOKEN: ${UPM_SITE_SMB_CONTROL_TOKEN:-}" in compose
