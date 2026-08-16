"""Fast schema regressions for retained Event deletion evidence."""

from upm_central.persistence.models import AuditRecord, OutboxEvent, SyncEvent


def _event_fk(model):
    return next(iter(model.__table__.c.event_id.foreign_keys), None)


def test_audit_event_uuid_is_historical_identity_not_a_live_foreign_key() -> None:
    assert _event_fk(AuditRecord) is None


def test_transport_event_foreign_keys_detach_without_losing_envelopes() -> None:
    assert _event_fk(OutboxEvent).ondelete == "SET NULL"
    assert _event_fk(SyncEvent).ondelete == "SET NULL"
