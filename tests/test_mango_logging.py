"""Logging must never emit a credential, regardless of the call site."""

import logging

from mango.core.logging import get_logger
from mango.core.redact import redact, redact_text

def test_log_records_are_redacted_before_emission(monkeypatch):
    """No log line may carry a credential, whatever the call site does.

    Providers log the URL they called, and a keyed API puts the key in it.
    Securing ~150 call sites individually is not a property anyone can hold, so
    the guarantee lives in a handler filter — this test is what keeps it there.

    The filter is exercised directly against its own stream rather than through
    the configured handler: that handler binds sys.stderr at import time, so
    neither capsys nor capfd observes it reliably, and a test that cannot fail
    is worse than no test.
    """
    import io

    from mango.core.logging import _RedactingFilter

    monkeypatch.setenv("FRED_API_KEY", "abcdef0123456789abcdef0123456789")
    url = "https://api.stlouisfed.org/x?api_key=abcdef0123456789abcdef0123456789"

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RedactingFilter())
    logger = logging.getLogger("mango_redaction_unit_test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.WARNING)

    logger.warning("lazy interpolation: %s", url)   # the form every call site uses
    logger.warning("preformatted: %s" % url)

    emitted = stream.getvalue()
    assert "abcdef0123456789abcdef0123456789" not in emitted
    assert emitted.count("REDACTED") == 2
