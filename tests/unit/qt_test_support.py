"""Bounded condition polling for tests that need Qt event delivery."""

from collections.abc import Callable
import math
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 3.0,
    *,
    fail_on_timeout: bool = True,
    message: str = "asynchronous Qt operation timed out",
) -> bool:
    """Poll a condition with a monotonic timeout in seconds, pumping Qt events.

    Predicate exceptions propagate. Teardown callers may explicitly request a
    False result on timeout instead of an AssertionError.
    """

    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Qt polling timeout must be finite, non-negative seconds")
    application = QCoreApplication.instance()
    if application is None:
        raise RuntimeError("Qt polling requires an existing application")
    deadline = time.monotonic() + timeout
    while not predicate():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if fail_on_timeout:
                raise AssertionError(message)
            return False
        application.processEvents()
        if predicate():
            return True
        QTest.qWait(max(1, min(10, math.ceil(remaining * 1000))))
    return True
