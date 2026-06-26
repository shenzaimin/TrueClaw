from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trueclaw.scheduler.leader import SchedulerLeaderLock


class SchedulerLeaderLockTest(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scheduler-leader.lock"
            leader = SchedulerLeaderLock(path, ttl_sec=10.0)
            self.assertTrue(leader.try_acquire())
            self.assertTrue(leader.is_leader)
            leader.release()
            self.assertFalse(leader.is_leader)

    def test_second_acquire_fails_while_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scheduler-leader.lock"
            first = SchedulerLeaderLock(path, ttl_sec=10.0)
            second = SchedulerLeaderLock(path, ttl_sec=10.0)
            self.assertTrue(first.try_acquire())
            self.assertFalse(second.try_acquire())
            first.release()
            self.assertTrue(second.try_acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
