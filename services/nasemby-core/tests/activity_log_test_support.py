from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import activity_log


class IsolatedActivityLogMixin:
    def setUp(self):
        super().setUp()
        self._activity_log_directory = TemporaryDirectory()
        self.addCleanup(self._activity_log_directory.cleanup)
        self._activity_log_patch = patch.object(
            activity_log,
            "LOG_PATH",
            Path(self._activity_log_directory.name) / "activity_log.jsonl",
        )
        self._activity_log_patch.start()
        self.addCleanup(self._activity_log_patch.stop)
