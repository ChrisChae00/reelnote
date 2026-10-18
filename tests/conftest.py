import pytest
from reelnote import cli

_REAL_NOTIFY = cli.notify


@pytest.fixture(autouse=True)
def notified(monkeypatch):
    """No test may show a real macOS notification: record messages instead."""
    monkeypatch.setenv("REELNOTE_NO_NOTIFY", "1")
    sent: list[str] = []
    monkeypatch.setattr(cli, "notify", sent.append)
    return sent


@pytest.fixture
def real_notify():
    """The unpatched notifier, for tests that stub subprocess themselves."""
    return _REAL_NOTIFY
