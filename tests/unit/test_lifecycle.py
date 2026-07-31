import unittest

from style_rotation.domain.enums import ArchiveStatus, RunStatus
from style_rotation.domain.lifecycle import ensure_archive_transition, ensure_run_transition


class LifecycleTests(unittest.TestCase):
    def test_valid_run_lifecycle(self) -> None:
        ensure_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
        ensure_run_transition(RunStatus.RUNNING, RunStatus.COMPLETED)

    def test_completed_run_is_terminal(self) -> None:
        with self.assertRaisesRegex(ValueError, "completed -> running"):
            ensure_run_transition(RunStatus.COMPLETED, RunStatus.RUNNING)

    def test_archive_requires_verification_before_restore_test(self) -> None:
        with self.assertRaisesRegex(ValueError, "pending -> restore_tested"):
            ensure_archive_transition(ArchiveStatus.PENDING, ArchiveStatus.RESTORE_TESTED)
        ensure_archive_transition(ArchiveStatus.PENDING, ArchiveStatus.VERIFIED)
        ensure_archive_transition(ArchiveStatus.VERIFIED, ArchiveStatus.RESTORE_TESTED)


if __name__ == "__main__":
    unittest.main()
