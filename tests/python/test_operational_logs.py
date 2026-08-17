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
