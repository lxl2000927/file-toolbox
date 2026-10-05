import threading
import unittest
from src.utils.preview_dispatcher import LatestPreviewWorker


class PreviewDispatcherTests(unittest.TestCase):
    def test_slow_preview_does_not_block_and_only_latest_pending_preview_runs(self):
        worker = LatestPreviewWorker()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        results = {}
        ran = []

        def slow(cancelled):
            started.set()
            self.assertTrue(release.wait(3))
            return "old"

        def complete(name):
            def done(result, error):
                results[name] = (result, error)
                if name == "latest":
                    finished.set()
            return done

        try:
            worker.submit(slow, complete("old"))
            self.assertTrue(started.wait(2))
            worker.submit(lambda _cancelled: ran.append("middle"), complete("middle"))
            worker.submit(lambda _cancelled: ran.append("latest") or "new", complete("latest"))
            release.set()
            self.assertTrue(finished.wait(3))
            self.assertEqual(ran, ["latest"])
            self.assertEqual(results["latest"], ("new", None))
            self.assertIsNotNone(results["middle"][1])
            self.assertIsNotNone(results["old"][1])
        finally:
            release.set()
            worker.close()

    def test_failed_preview_does_not_prevent_the_next_request(self):
        worker = LatestPreviewWorker()
        first_done = threading.Event()
        next_done = threading.Event()
        results = []

        def fail(_cancelled):
            raise ValueError("bad reference")

        try:
            worker.submit(fail, lambda result, error: (results.append(error), first_done.set()))
            self.assertTrue(first_done.wait(2))
            worker.submit(lambda _: "ok", lambda result, error: (results.append(result), next_done.set()))
            self.assertTrue(next_done.wait(2))
            self.assertIsInstance(results[0], ValueError)
            self.assertEqual(results[1], "ok")
        finally:
            worker.close()
