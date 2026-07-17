import socket

import pytest


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Block outbound network sockets in unit tests (AGENTS.md test-isolation rule).

    Unit tests must not reach Postgres, LLM providers, market data, news, search, or the
    harness. SQLite in-memory and pure functions do not open sockets, so they are
    unaffected. Opt out per-test with ``@pytest.mark.allow_network`` (reviewed exceptions
    only); ``@pytest.mark.integration`` tests live under tests/integration and are opt-in.
    """

    if request.node.get_closest_marker("allow_network"):
        yield
        return

    def _guarded(*args, **kwargs):
        raise RuntimeError(
            "network access blocked in unit tests; mark @pytest.mark.allow_network to opt in"
        )

    monkeypatch.setattr(socket, "socket", _guarded)
    yield
