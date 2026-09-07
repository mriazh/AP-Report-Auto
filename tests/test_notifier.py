"""GOWA notification adapter.

The request shape follows the public, non-secret convention already used in the
office tooling: ``POST {base}/send/message`` with ``{"phone", "message"}`` and an
optional ``X-Device-Id`` header. No credential is read from another project.
"""

from __future__ import annotations


from huawei_ap_report.notifier import SEND_PATH, GowaNotifier, NullNotifier
from huawei_ap_report.settings import NotifySettings


class FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class FakeSession:
    def __init__(self, response=None, raises: Exception | None = None):
        self.response = response if response is not None else FakeResponse()
        self.raises = raises
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None, verify=True):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout, "verify": verify})
        if self.raises is not None:
            raise self.raises
        return self.response


def _settings(**overrides) -> NotifySettings:
    base = dict(
        enabled=True,
        base_url="https://gateway.example",
        device_id="device-1",
        group_jid="120363000@g.us",
        timeout_seconds=15,
    )
    base.update(overrides)
    return NotifySettings(**base)


def test_posts_to_send_message_with_phone_and_message():
    session = FakeSession()

    delivered = GowaNotifier(_settings(), session=session).send("hello group")

    assert delivered is True
    call = session.calls[0]
    assert call["url"] == f"https://gateway.example{SEND_PATH}"
    assert call["json"] == {"phone": "120363000@g.us", "message": "hello group"}
    assert call["headers"]["X-Device-Id"] == "device-1"
    assert call["timeout"] == 15


def test_trailing_slash_in_base_url_is_normalised():
    session = FakeSession()

    GowaNotifier(_settings(base_url="https://gateway.example/"), session=session).send("x")

    assert session.calls[0]["url"] == f"https://gateway.example{SEND_PATH}"


def test_device_id_header_is_omitted_when_unset():
    session = FakeSession()

    GowaNotifier(_settings(device_id=""), session=session).send("x")

    assert "X-Device-Id" not in session.calls[0]["headers"]


def test_network_failure_is_reported_not_raised():
    session = FakeSession(raises=OSError("connection refused"))

    assert GowaNotifier(_settings(), session=session).send("x") is False


def test_http_error_is_reported_not_raised():
    session = FakeSession(response=FakeResponse(status_code=500))

    assert GowaNotifier(_settings(), session=session).send("x") is False


def test_disabled_notifier_does_not_call_the_gateway():
    session = FakeSession()

    assert GowaNotifier(_settings(enabled=False), session=session).send("x") is False
    assert session.calls == []


def test_incomplete_configuration_skips_silently():
    session = FakeSession()

    assert GowaNotifier(_settings(base_url=""), session=session).send("x") is False
    assert GowaNotifier(_settings(group_jid=""), session=session).send("x") is False
    assert session.calls == []


def test_verification_is_on_by_default():
    session = FakeSession()

    GowaNotifier(_settings(), session=session).send("x")

    assert session.calls[0]["verify"] is True


def test_ca_bundle_is_passed_as_the_verify_target():
    session = FakeSession()

    GowaNotifier(_settings(), session=session, ca_bundle="/etc/ssl/office.pem").send("x")

    assert session.calls[0]["verify"] == "/etc/ssl/office.pem"


def test_null_notifier_is_a_no_op():
    assert NullNotifier().send("anything") is False


def test_failures_never_leak_the_group_jid():
    """A gateway error may name the transport, never the group or the body."""

    import logging

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("huawei_ap_report.notifier")
    handler = Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        session = FakeSession(raises=OSError("boom"))
        GowaNotifier(_settings(), session=session).send("secret message body")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    logged = "\n".join(record.getMessage() for record in records)
    assert "120363000@g.us" not in logged
    assert "secret message body" not in logged
    assert "boom" in logged