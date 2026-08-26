from uuid import uuid4

from upm_central.operational_logs import record_log
from upm_shared.operational_logs import redact_context


def test_structured_log_context_recursively_redacts_secrets() -> None:
    value = redact_context({
        "filename": "deck.pptx",
        "authorization": "Bearer secret",
        "nested": {"csrf_token": "secret", "safe": "visible"},
        "items": [{"password": "secret"}],
    })
    assert value == {
        "filename": "deck.pptx",
        "authorization": "[REDACTED]",
        "nested": {"csrf_token": "[REDACTED]", "safe": "visible"},
        "items": [{"password": "[REDACTED]"}],
    }


def test_record_log_folds_unknown_ids_into_context() -> None:
    class Session:
        def __init__(self) -> None:
            self.item = None

        def add(self, item) -> None:
            self.item = item

        def flush(self) -> None:
            raise AssertionError("flush was disabled")

    session = Session()
    site_id = uuid4()
    item = record_log(
        session,
        service="test",
        event_type="test.event",
        message="test",
        event_id=uuid4(),
        site_id=site_id,
        context={"safe": True},
        flush=False,
    )

    assert session.item is item
    assert item.context == {"safe": True, "site_id": str(site_id)}
    assert not hasattr(item, "site_id")
