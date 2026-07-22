import unittest

from app.health_state_runtime import SchedulerStatusRegistry, combine_health


class HealthStateRuntimeTests(unittest.TestCase):
    def test_health_priority_keeps_actionable_failure_above_stale_evidence(self):
        self.assertEqual(
            combine_health("normal", "protected", "evidence_insufficient", "action_required"),
            "action_required",
        )
        self.assertEqual(combine_health("normal", "protected", "waiting"), "waiting")

    def test_scheduler_registry_distinguishes_enabled_from_started_and_heartbeat(self):
        ticks = iter(("2026-07-22T01:00:00Z", "2026-07-22T01:00:01Z", "2026-07-22T01:00:02Z"))
        registry = SchedulerStatusRegistry(clock=lambda: next(ticks))
        registry.register("subscription-task", enabled=True)
        self.assertFalse(registry.snapshot("subscription-task")["started"])
        registry.mark_started("subscription-task")
        registry.mark_run("subscription-task")
        state = registry.snapshot("subscription-task")
        self.assertTrue(state["started"])
        self.assertEqual(state["lastRunAt"], "2026-07-22T01:00:02Z")

    def test_unknown_scheduler_has_safe_defaults(self):
        state = SchedulerStatusRegistry(clock=lambda: "2026-07-22T01:00:00Z").snapshot("missing")
        self.assertFalse(state["enabled"])
        self.assertFalse(state["started"])
        self.assertEqual(state["lastRunAt"], "")


if __name__ == "__main__":
    unittest.main()
