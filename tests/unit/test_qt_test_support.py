"""Tests for the shared seconds-based Qt condition wait."""

import time
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from qt_test_support import wait_until


class QtConditionWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_immediate_condition_needs_no_delay(self):
        self.assertTrue(wait_until(lambda: True, timeout=0.0))

    def test_delivers_events_until_condition_and_uses_seconds(self):
        completed = []
        QTimer.singleShot(40, lambda: completed.append(True))
        self.assertTrue(wait_until(lambda: bool(completed), timeout=0.5))

    def test_timeout_fails_with_callers_message(self):
        started = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "synthetic timeout"):
            wait_until(lambda: False, timeout=0.02, message="synthetic timeout")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_teardown_can_explicitly_return_false_without_failure(self):
        self.assertFalse(wait_until(lambda: False, timeout=0.0, fail_on_timeout=False))

    def test_teardown_success_still_returns_true(self):
        self.assertTrue(wait_until(lambda: True, fail_on_timeout=False))

    def test_predicate_exceptions_are_not_hidden(self):
        def broken():
            raise RuntimeError("synthetic predicate failure")

        with self.assertRaisesRegex(RuntimeError, "synthetic predicate failure"):
            wait_until(broken)

    def test_rejects_invalid_timeout(self):
        for timeout in (-1.0, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                wait_until(lambda: True, timeout=timeout)
