import unittest

from flask import Flask

from app.system_metrics_runtime import register_system_metrics


class SystemMetricsRuntimeTests(unittest.TestCase):
    def test_metrics_are_whitelisted_and_cached(self):
        calls = []
        now = [1_700_000_000.0]

        def sampler():
            calls.append(True)
            return {
                "cpu": {"percent": 18.25},
                "memory": {"total": 1000, "used": 400, "available": 600, "percent": 40},
                "disk": {"path": "/secret/data", "total": 5000, "used": 2500, "free": 2500, "percent": 50},
                "network": {"down_bps": 100, "up_bps": 50, "rx_total": 1000, "tx_total": 500},
                "emby": {"libraries": [{"name": "private"}], "token": "must-not-escape"},
            }

        app = Flask(__name__)
        register_system_metrics(app, sampler=sampler, clock=lambda: now[0])
        client = app.test_client()

        first = client.get("/api/v2/system/metrics")
        second = client.get("/api/v2/system/metrics")
        now[0] += 31
        third = client.get("/api/v2/system/metrics")

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["cached"])
        self.assertTrue(second.get_json()["cached"])
        self.assertFalse(third.get_json()["cached"])
        self.assertEqual(len(calls), 2)
        serialized = first.get_data(as_text=True)
        self.assertNotIn("/secret/data", serialized)
        self.assertNotIn("private", serialized)
        self.assertNotIn("must-not-escape", serialized)
        self.assertEqual(first.get_json()["network"]["downBps"], 100)

    def test_metrics_failure_is_fixed_and_safe(self):
        app = Flask(__name__)
        register_system_metrics(app, sampler=lambda: (_ for _ in ()).throw(RuntimeError("secret path")))
        response = app.test_client().get("/api/v2/system/metrics")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "SYSTEM_METRICS_UNAVAILABLE")
        self.assertNotIn("secret path", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
