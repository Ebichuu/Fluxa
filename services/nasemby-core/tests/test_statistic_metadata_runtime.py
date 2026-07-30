import unittest

from app.statistic_metadata_runtime import statistic_metadata


class StatisticMetadataRuntimeTests(unittest.TestCase):
    def test_builds_fixed_public_shape(self):
        self.assertEqual(statistic_metadata(
            scope="current_unique_task_chains",
            unit="task_chain",
            observed_at="2026-07-30T04:00:00Z",
            confirmation="partial",
        ), {
            "scope": "current_unique_task_chains",
            "unit": "task_chain",
            "observedAt": "2026-07-30T04:00:00Z",
            "confirmation": "partial",
        })

    def test_rejects_unknown_confirmation_value(self):
        with self.assertRaises(ValueError):
            statistic_metadata(scope="tasks", unit="task", observed_at="", confirmation="guess")


if __name__ == "__main__":
    unittest.main()
