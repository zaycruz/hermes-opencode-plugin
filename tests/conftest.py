"""Override the global 30s test timeout for integration tests.

OpenCode integration tests involve real subprocess execution and can take
60-300 seconds. The parent conftest.py sets a 30s SIGALRM that kills them.
This conftest overrides that with a 600s limit.
"""

import signal
import sys

import pytest


@pytest.fixture(autouse=True)
def _enforce_test_timeout():
    """600 second timeout for integration tests (overrides the 30s global)."""
    if sys.platform == "win32":
        yield
        return

    def _handler(signum, frame):
        raise TimeoutError("Integration test exceeded 600 second timeout")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(600)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)
