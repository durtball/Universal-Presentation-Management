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
        assert f"UPM_{deployment.upper()}_SMB_BIND_ADDRESS" in service
        assert ':445:445"' in service
        assert "ports:" not in postgres
        assert f"  {deployment}-internal:\n    internal: true" in compose


def test_central_and_site_default_to_dynamic_smb_interfaces() -> None:
    for deployment in ("central", "site"):
        compose = (ROOT / f"docker-compose.{deployment}.yml").read_text()
        assert f"UPM_{deployment.upper()}_SMB_INTERFACES:-auto" in compose
        assert "SMB_INTERFACES:-lo eth0" not in compose


def test_interface_discovery_includes_all_active_networks(tmp_path, monkeypatch) -> None:
    import upm_smb_edge.interfaces as interfaces

    for name, state in (("lo", "unknown"), ("eth0", "up"), ("eth1", "up"), ("eth2", "down")):
        path = tmp_path / name
        path.mkdir()
        (path / "operstate").write_text(state)
    monkeypatch.setattr(
        interfaces.socket,
        "if_nameindex",
        lambda: [(1, "lo"), (2, "eth1"), (3, "eth0"), (4, "eth2")],
    )
    assert interfaces.active_interfaces(tmp_path) == ["lo", "eth0", "eth1"]


def test_startup_repairs_share_roots_without_recursive_reownership() -> None:
    entrypoint = (ROOT / "smb-edge/entrypoint.sh").read_text()
    assert 'UPM_SMB_INTERFACES:=auto' in entrypoint
    assert "python -m upm_smb_edge.interfaces" in entrypoint
    assert "testparm -s /etc/samba/smb.conf" in entrypoint
    assert "chgrp upm_read_only /shares/presentations" not in entrypoint
    assert "chmod 2755 /shares/presentations" not in entrypoint
    assert "chgrp upm_operator /shares/incoming" in entrypoint
    assert "chmod 2775 /shares/incoming" in entrypoint
    assert "chgrp upm_administrator /shares/trash" in entrypoint
    assert "chmod 2770 /shares/trash" in entrypoint
    assert "chgrp -R" not in entrypoint
    assert "chown -R" not in entrypoint
    assert "chmod -R" not in entrypoint


def test_samba_and_filesystem_group_semantics_agree() -> None:
    config = (ROOT / "smb-edge/smb.conf").read_text()
    presentation = config.split("[Presentations]", 1)[1].split("[Incoming]", 1)[0]
    incoming = config.split("[Incoming]", 1)[1].split("[Trash]", 1)[0]
    trash = config.split("[Trash]", 1)[1]
    assert "read only = yes" in presentation
    assert "force group = upm_operator" in incoming
    assert "directory mask = 2775" in incoming
    assert "force directory mode = 2770" in incoming
    assert "force group = upm_administrator" in trash
    assert "directory mask = 2770" in trash
    assert "@upm_operator" not in trash


def test_health_rejects_incorrect_share_group_or_mode(monkeypatch) -> None:
    from types import SimpleNamespace

    import upm_smb_edge.api as api

    gids = {
        name: index
        for index, name in enumerate(
            ("upm_read_only", "upm_operator", "upm_administrator"), start=100
        )
    }
    monkeypatch.setattr(api.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=gids[name]))
    monkeypatch.setattr(
        api.os,
        "stat",
        lambda path: SimpleNamespace(
            st_gid=(
                gids[api.SHARE_REQUIREMENTS[path][0]]
                if path in api.SHARE_REQUIREMENTS
                else 0
            ),
            st_mode=(
                api.SHARE_REQUIREMENTS[path][1]
                if path in api.SHARE_REQUIREMENTS
                else 0o40755
            ),
        ),
    )
    assert api.share_permission_errors() == []

    monkeypatch.setattr(api.os, "stat", lambda _path: SimpleNamespace(st_gid=0, st_mode=0o40755))
    errors = api.share_permission_errors()
    assert any("incorrect group" in error for error in errors)
    assert any("expected 2775" in error for error in errors)


def test_health_validates_read_only_presentations_without_requiring_ownership(monkeypatch) -> None:
    from types import SimpleNamespace

    import upm_smb_edge.api as api

    gids = {"upm_operator": 101, "upm_administrator": 102}
    monkeypatch.setattr(api.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=gids[name]))

    def stat_path(path: str):
        if path == "/shares/presentations":
            return SimpleNamespace(st_gid=0, st_mode=0o40755)
        group, mode = api.SHARE_REQUIREMENTS[path]
        return SimpleNamespace(st_gid=gids[group], st_mode=0o40000 | mode)

    monkeypatch.setattr(api.os, "stat", stat_path)
    assert api.share_permission_errors() == []

    def inaccessible_presentations(path: str):
        if path == "/shares/presentations":
            return SimpleNamespace(st_gid=0, st_mode=0o40700)
        return stat_path(path)

    monkeypatch.setattr(api.os, "stat", inaccessible_presentations)
    assert api.share_permission_errors() == [
        "/shares/presentations: mode 0700, requires read/traverse access"
    ]


def test_readiness_checks_configuration_and_tcp_445_on_every_interface(monkeypatch) -> None:
    from types import SimpleNamespace

    import upm_smb_edge.api as api

    checked = []
    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        api,
        "configured_interface_addresses",
        lambda: [("lo", "127.0.0.1"), ("eth0", "172.19.0.4"), ("eth1", "172.20.0.5")],
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def connect(address, timeout):
        checked.append((address, timeout))
        return Connection()

    monkeypatch.setattr(api.socket, "create_connection", connect)
    assert api.smb_readiness_errors() == []
    assert checked == [
        (("127.0.0.1", 445), 1),
        (("172.19.0.4", 445), 1),
        (("172.20.0.5", 445), 1),
    ]


def test_readiness_rejects_invalid_config_or_unbound_published_interface(monkeypatch) -> None:
    from types import SimpleNamespace

    import upm_smb_edge.api as api

    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(
        api,
        "configured_interface_addresses",
        lambda: [("lo", "127.0.0.1"), ("eth1", "172.20.0.5")],
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def connect(address, timeout):
        del timeout
        if address[0] == "172.20.0.5":
            raise ConnectionRefusedError
        return Connection()

    monkeypatch.setattr(api.socket, "create_connection", connect)
    assert api.smb_readiness_errors() == [
        "Samba configuration is invalid",
        "TCP 445 is not listening on eth1",
    ]


def test_readiness_accepts_single_network_container(monkeypatch) -> None:
    from types import SimpleNamespace

    import upm_smb_edge.api as api

    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        api,
        "configured_interface_addresses",
        lambda: [("lo", "127.0.0.1"), ("eth0", "172.20.0.5")],
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(api.socket, "create_connection", lambda *_args, **_kwargs: Connection())
    assert api.smb_readiness_errors() == []


def test_higher_roles_receive_incoming_filesystem_group(monkeypatch) -> None:
    import upm_smb_edge.api as api
    from upm_smb_edge.accounts import effective_groups

    assignments = []
    monkeypatch.setattr(api, "TOKEN", "control-token")
    monkeypatch.setattr(
        api, "assign_role", lambda username, role: assignments.append((username, role))
    )
    monkeypatch.setattr(api.subprocess, "run", lambda _command, **_kwargs: None)
    api.credential(
        "manager",
        api.Credential(username="manager", password="a secure smb password", role="manager"),
        "Bearer control-token",
    )
    assert assignments == [("manager", "manager")]
    assert effective_groups("manager") == {"manager", "operator"}
    assert effective_groups("administrator") == {"administrator", "operator"}
    assert effective_groups("read_only") == {"read_only"}


def test_role_map_restores_membership_after_container_recreation(tmp_path, monkeypatch) -> None:
    import upm_smb_edge.accounts as accounts

    role_map = tmp_path / "persistent-passdb" / "upm-role-map.json"
    applied = []
    monkeypatch.setattr(accounts, "ROLE_MAP_PATH", role_map)
    monkeypatch.setattr(
        accounts, "apply_role", lambda username, role: applied.append((username, role))
    )
    accounts.assign_role("operator", "operator")
    assert role_map.stat().st_mode & 0o777 == 0o600
    applied.clear()
    accounts.restore()
    assert applied == [("operator", "operator")]
