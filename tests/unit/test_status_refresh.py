import threading
import time
import unittest

from PySide6.QtCore import QTimer
from qt_test_support import wait_until
from PySide6.QtWidgets import QApplication

from moltage.gui.status_refresh import (
    STATUS_REFRESH_TIMEOUT_MS,
    StatusRefreshSession,
    StatusRefreshTimeout,
)


class StatusRefreshSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _wait_until(self, predicate, timeout=2.0):
        wait_until(predicate, timeout, message="status refresh test timed out")

    def _blocked_session(self, *, timeout_ms=1000):
        started, release, returned = (threading.Event() for _ in range(3))
        tokens = []

        def operation(stop_token, progress):
            tokens.append(stop_token)
            progress("Reading synthetic task status")
            started.set()
            release.wait(timeout=5)
            returned.set()
            return "synthetic success"

        session = StatusRefreshSession(operation, timeout_ms=timeout_ms)
        self.addCleanup(release.set)
        self.addCleanup(session.request_stop)
        outcomes = []
        session.succeeded.connect(lambda result: outcomes.append(("success", result)))
        session.failed.connect(lambda error: outcomes.append(("failure", error)))
        session.stopped.connect(lambda: outcomes.append(("stopped", None)))
        session.finished.connect(lambda worker: outcomes.append(("finished", worker)))
        return session, started, release, returned, tokens, outcomes

    def test_default_is_one_minute_not_a_periodic_refresh(self):
        session = StatusRefreshSession(lambda _token, _progress: None)
        self.assertEqual(STATUS_REFRESH_TIMEOUT_MS, 60_000)
        self.assertEqual(session._timer.interval(), 60_000)
        self.assertTrue(session._timer.isSingleShot())
        self.assertFalse(session._timer.isActive())

    def test_stop_finishes_immediately_despite_uncooperative_io(self):
        session, started, release, returned, tokens, outcomes = self._blocked_session()
        session.start()
        self._wait_until(started.is_set)
        session.request_stop()

        self.assertEqual([kind for kind, _ in outcomes], ["stopped", "finished"])
        self.assertTrue(tokens[0].is_requested)
        self.assertFalse(session._timer.isActive())
        self.assertFalse(returned.is_set())
        heartbeat = threading.Event()
        QTimer.singleShot(0, heartbeat.set)
        self._wait_until(heartbeat.is_set)
        session.request_stop()
        release.set()
        self._wait_until(returned.is_set)
        self.application.processEvents()
        self.assertEqual([kind for kind, _ in outcomes], ["stopped", "finished"])

    def test_timeout_finishes_without_waiting_or_marking_a_task_failed(self):
        session, started, release, returned, tokens, outcomes = self._blocked_session(timeout_ms=50)
        session.start()
        self._wait_until(lambda: len(outcomes) == 2)
        self.assertTrue(started.is_set())
        self.assertFalse(returned.is_set())
        self.assertTrue(tokens[0].is_requested)
        self.assertEqual([kind for kind, _ in outcomes], ["failure", "finished"])
        self.assertIsInstance(outcomes[0][1], StatusRefreshTimeout)
        self.assertIn("Last known task states were retained", str(outcomes[0][1]))
        self.assertIn("No calculation was cancelled", str(outcomes[0][1]))
        release.set()
        self._wait_until(returned.is_set)
        self.application.processEvents()
        self.assertEqual(len(outcomes), 2)

    def test_queued_late_signals_are_ignored_after_stop(self):
        session, started, _release, _returned, _tokens, outcomes = self._blocked_session()
        progress = []
        session.progress.connect(progress.append)
        session.start()
        self._wait_until(started.is_set)
        session.request_stop()
        prior_progress = tuple(progress)
        session._results.progress.emit("late old progress")
        session._results.failed.emit(RuntimeError("late old error"))
        session._results.succeeded.emit("late old result")
        self.assertEqual(tuple(progress), prior_progress)
        self.assertEqual([kind for kind, _ in outcomes], ["stopped", "finished"])

    def test_result_received_after_deadline_is_rejected_before_timer_delivery(self):
        session, started, _release, _returned, _tokens, outcomes = self._blocked_session()
        session.start()
        self._wait_until(started.is_set)
        session._deadline = time.monotonic() - 1
        session._results.succeeded.emit("too late")
        self.assertEqual([kind for kind, _ in outcomes], ["failure", "finished"])
        self.assertIsInstance(outcomes[0][1], StatusRefreshTimeout)

    def test_success_stops_timer_and_delivers_exactly_once(self):
        session, started, release, _returned, _tokens, outcomes = self._blocked_session()
        session.start()
        self._wait_until(started.is_set)
        release.set()
        self._wait_until(lambda: len(outcomes) == 2)
        self.assertEqual([kind for kind, _ in outcomes], ["success", "finished"])
        self.assertEqual(outcomes[0][1], "synthetic success")
        self.assertFalse(session._timer.isActive())
        session._timed_out()
        session.request_stop()
        self.assertEqual(len(outcomes), 2)

    def test_failure_stops_timer_without_silent_fallback(self):
        error = RuntimeError("synthetic status failure")

        def operation(_token, _progress):
            raise error

        session = StatusRefreshSession(operation)
        failures, finished = [], []
        session.failed.connect(failures.append)
        session.finished.connect(finished.append)
        session.start()
        self._wait_until(lambda: bool(finished))
        self.assertEqual(failures, [error])
        self.assertEqual(finished, [session])
        self.assertFalse(session._timer.isActive())

    def test_invalid_deadline_and_duplicate_start_are_rejected(self):
        for timeout in (0, -1, True, 1.5):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                StatusRefreshSession(lambda _token, _progress: None, timeout_ms=timeout)
        session = StatusRefreshSession(lambda _token, _progress: None)
        session.start()
        self.addCleanup(session.request_stop)
        with self.assertRaises(RuntimeError):
            session.start()
