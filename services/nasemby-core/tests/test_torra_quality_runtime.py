from __future__ import annotations

import unittest
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from app.torra_quality_runtime import TorraQualityBlocked, TorraQualityClient
from app.torra_read_runtime import TorraReadConfig


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, urlsplit(url).path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def success(payload):
    return FakeResponse(payload={"success": True, "data": payload})


class TorraQualityRuntimeTests(unittest.TestCase):
    def test_token_auth_submits_analysis_and_download_with_verified_payloads(self):
        session = FakeSession([
            success({"job_id": "analysis-job-1"}),
            success({"job_id": "download-job-1"}),
        ])
        client = TorraQualityClient(
            TorraReadConfig(base_url="http://torra.example.test/", token="fixed-token"),
            session=session,
        )

        self.assertEqual(client.submit_analysis("subscription/1"), "analysis-job-1")
        self.assertEqual(
            client.submit_download("subscription/1", "analysis-9", {"row-1": "candidate-8"}),
            "download-job-1",
        )
        self.assertEqual(session.requests[0][1], "/api/v1/subscriptions/rewash/subscription%2F1")
        self.assertEqual(session.requests[0][2]["headers"]["Authorization"], "Bearer fixed-token")
        self.assertIsNone(session.requests[0][2]["json"])
        self.assertEqual(session.requests[1][1], "/api/v1/subscriptions/rewash/subscription%2F1/download")
        self.assertEqual(session.requests[1][2]["json"], {
            "analysis_id": "analysis-9",
            "selected_candidates": {"row-1": "candidate-8"},
            "force_push": True,
        })

    def test_password_auth_relogs_once_after_write_is_unauthorized(self):
        session = FakeSession([
            FakeResponse(payload={"access_token": "token-one"}),
            FakeResponse(status=403, payload={}),
            FakeResponse(payload={"access_token": "token-two"}),
            success({"job_id": "analysis-job-2"}),
        ])
        client = TorraQualityClient(TorraReadConfig(
            base_url="http://torra.example.test",
            username="user",
            password="password",
        ), session=session)

        self.assertEqual(client.submit_analysis("subscription-2"), "analysis-job-2")
        self.assertEqual([request[0] for request in session.requests], ["POST", "POST", "POST", "POST"])
        self.assertEqual(session.requests[1][2]["headers"]["Authorization"], "Bearer token-one")
        self.assertEqual(session.requests[3][2]["headers"]["Authorization"], "Bearer token-two")

    def test_job_query_maps_all_verified_statuses(self):
        statuses = ["pending", "running", "success", "failed", "cancelled"]
        responses = [
            success({"status": status, **({"result": {"analysis_id": "a", "rows": []}} if status == "success" else {})})
            for status in statuses
        ]
        session = FakeSession(responses)
        client = TorraQualityClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )

        actual = [client.get_job(f"job-{index}")["status"] for index in range(len(statuses))]

        self.assertEqual(actual, statuses)
        self.assertEqual([request[1] for request in session.requests], [f"/api/v1/jobs/job-{i}" for i in range(5)])

    def test_analysis_selects_only_highest_positive_upgrade_per_row(self):
        selection = TorraQualityClient.select_upgrade_candidates({
            "status": "success",
            "result": {
                "analysis_id": "analysis-1",
                "rows": [
                    {
                        "row_id": "row-1",
                        "library_meta_weight_score": 60,
                        "candidates": [
                            {"candidate_id": "not-upgrade", "is_upgrade": False, "meta_weight_score": 99},
                            {"candidate_id": "lower", "is_upgrade": True, "meta_weight_score": 70},
                            {"candidate_id": "highest", "is_upgrade": True, "meta_weight_score": 85},
                        ],
                    },
                    {
                        "row_id": "row-2",
                        "library_meta_weight_score": 80,
                        "candidates": [
                            {"candidate_id": "equal", "is_upgrade": True, "meta_weight_score": 80},
                            {"candidate_id": "lower", "is_upgrade": True, "meta_weight_score": 79},
                        ],
                    },
                    {"row_id": "row-3", "library_meta_weight_score": 10, "candidates": []},
                ],
            },
        })

        self.assertEqual(selection, {
            "analysis_id": "analysis-1",
            "selected_candidates": {"row-1": "highest"},
            "row_count": 3,
            "selected_count": 1,
        })

    def test_unknown_or_incomplete_structures_are_blocked(self):
        client = TorraQualityClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=FakeSession([success({"status": "mystery"})]),
        )
        with self.assertRaises(TorraQualityBlocked):
            client.get_job("job-unknown")
        with self.assertRaises(TorraQualityBlocked):
            TorraQualityClient.select_upgrade_candidates({
                "status": "success",
                "result": {"analysis_id": "", "rows": []},
            })
        with self.assertRaises(TorraQualityBlocked):
            TorraQualityClient.select_upgrade_candidates({
                "status": "success",
                "result": {
                    "analysis_id": "analysis-1",
                    "rows": [{
                        "row_id": "row-1",
                        "library_meta_weight_score": 1,
                        "candidates": [{"candidate_id": "candidate-1", "meta_weight_score": 2}],
                    }],
                },
            })

    def test_network_errors_do_not_expose_credentials_or_upstream_urls(self):
        session = FakeSession([
            requests.ConnectionError("failed http://torra.invalid?token=must-not-escape"),
        ])
        client = TorraQualityClient(
            TorraReadConfig(base_url="http://torra.invalid", token="must-not-escape"),
            session=session,
        )

        with self.assertRaisesRegex(RuntimeError, "Torra 写入请求失败") as raised:
            client.submit_analysis("subscription-1")

        self.assertNotIn("must-not-escape", str(raised.exception))
        self.assertNotIn("torra.invalid", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
