"""Security and deployment-boundary regression tests for the SMB edge."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_samba_requires_smb3_and_never_maps_guests() -> None:
    config = (ROOT / "smb-edge/smb.conf").read_text()
    assert "server min protocol = SMB3_00" in config
    assert "server max protocol = SMB3_11" in config
    assert "map to guest = Never" in config
    assert "restrict anonymous = 2" in config
    assert "guest ok = no" in config
    assert "server services = -dns, -nbt" in config


def test_managed_presentation_view_is_read_only() -> None:
    config = (ROOT / "smb-edge/smb.conf").read_text()
    presentation = config.split("[Presentations]", 1)[1].split("[Incoming]", 1)[0]
    incoming = config.split("[Incoming]", 1)[1].split("[Trash]", 1)[0]
    assert "read only = yes" in presentation
    assert "read only = no" in incoming
    assert "@upm_read_only" not in incoming


def test_plaintext_password_is_only_sent_to_smbpasswd_stdin() -> None:
    source = (ROOT / "smb-edge/python/src/upm_smb_edge/api.py").read_text()
    assert 'input=f"{payload.password}\\n{payload.password}\\n"' in source
    assert '"smbpasswd", "-s", "-a"' in source
    assert "password_hash" not in source


def test_central_and_site_have_independent_smb_state() -> None:
    central = (ROOT / "docker-compose.central.yml").read_text()
    site = (ROOT / "docker-compose.site.yml").read_text()
    assert "central-smb-state:/var/lib/samba/private" in central
    assert "site-smb-state:/var/lib/samba/private" in site
    assert "central-smb-presentations:/shares/presentations:ro" in central
    assert "site-smb-presentations:/shares/presentations:ro" in site


def test_smb_services_join_edge_and_internal_networks_without_exposing_postgres() -> None:
    for deployment in ("central", "site"):
        compose = (ROOT / f"docker-compose.{deployment}.yml").read_text()
        service = compose.split(f"  {deployment}-smb:", 1)[1].split(
            f"\n  {deployment}-discovery:", 1
        )[0]
        postgres = compose.split(f"  {deployment}-postgres:", 1)[1].split("\nnetworks:", 1)[0]
        assert f"- {deployment}-edge" in service
        assert f"- {deployment}-internal" in service
        assert f'UPM_{deployment.upper()}_SMB_BIND_ADDRESS' in service
        assert ':445:445"' in service
        assert "ports:" not in postgres
        assert f"  {deployment}-internal:\n    internal: true" in compose
