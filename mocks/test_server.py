#!/usr/bin/env python3
"""Exercise HTTP boundary, schema denial and replay behavior of the synthetic mock."""
import copy
import importlib.util
import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

path = Path(__file__).with_name("server.py")
spec = importlib.util.spec_from_file_location("contract_mock", path)
mock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mock)


class MockContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock.ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, path, body=None, headers=None, method=None):
        merged = {"X-Mock-Mode": "synthetic", "Authorization": "Bearer synthetic-staff",
                  "Content-Type": "application/json", **(headers or {})}
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=merged, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as err:
            response = err
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_health_declares_non_business_backend(self):
        status, body, _ = self.request("/health")
        self.assertEqual(status, 200)
        self.assertFalse(body["business_logic"])

    def test_replay_and_conflicting_body(self):
        body = copy.deepcopy(mock.FIXTURES["create_application"]["request"])
        headers = {"Idempotency-Key": "mock-replay-test-01"}
        status, first, _ = self.request("/api/v1/applications", body, headers)
        self.assertEqual(status, 201)
        status, second, returned = self.request("/api/v1/applications", body, headers)
        self.assertEqual(status, 201)
        self.assertEqual(first, second)
        self.assertEqual(returned["Idempotency-Replayed"], "true")
        body["intent"] = "renewal"
        status, result, _ = self.request("/api/v1/applications", body, headers)
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "IDEMPOTENCY_PAYLOAD_CONFLICT")

    def test_unknown_field_is_rejected(self):
        body = copy.deepcopy(mock.FIXTURES["create_customer"]["request"])
        body["grant_entitlement_now"] = True
        status, _, _ = self.request("/api/v1/customers", body, {"Idempotency-Key": "mock-deny-unknown"})
        self.assertEqual(status, 422)

    def test_missing_concurrency_precondition_is_rejected(self):
        body = mock.FIXTURES["update_customer"]["request"]
        status, result, _ = self.request("/api/v1/customers/11111111-1111-4111-8111-111111111111", body,
                                        {"Idempotency-Key": "mock-cas-required"}, "PATCH")
        self.assertEqual(status, 428)
        self.assertEqual(result["code"], "IF_MATCH_REQUIRED")

    def test_signed_publication_cannot_be_manual_command(self):
        status, _, _ = self.request("/api/v1/publications", {}, {"Idempotency-Key": "mock-no-publish"})
        self.assertEqual(status, 404)

    def test_ambiguous_outcome_requires_lookup_not_blind_retry(self):
        body = mock.FIXTURES["create_payment_intent"]["request"]
        status, result, _ = self.request("/api/v1/finance/payment-intents", body,
                                       {"Idempotency-Key": "mock-unknown-outcome", "X-Mock-Scenario": "ambiguous_timeout"})
        self.assertEqual(status, 503)
        self.assertFalse(result["retryable"])
        self.assertEqual(result["code"], "DEPENDENCY_OUTCOME_UNKNOWN_QUERY_FIRST")

    def test_service_response_needs_approval_record(self):
        body = copy.deepcopy(mock.FIXTURES["send_service_response"]["request"])
        body.pop("approval_id")
        status, result, _ = self.request(
            "/api/v1/service-cases/11111111-1111-4111-8111-111111111111/responses", body,
            {"Idempotency-Key": "mock-response-no-approval", "If-Match": '"1"'})
        self.assertEqual(status, 422)
        self.assertEqual(result["code"], "SCHEMA_VALIDATION_FAILED")

    def test_fake_integration_signature_rejected(self):
        body = mock.FIXTURES["receive_payment_webhook"]["request"]
        status, _, _ = self.request("/api/v1/webhooks/payments", body,
                                    {"Idempotency-Key": "mock-invalid-signature", "X-Webhook-Timestamp": "1795305600",
                                     "X-Webhook-Nonce": "synthetic-nonce-0001", "X-Integration-Key-Id": "synthetic-id",
                                     "X-Webhook-Signature": "wrong-signature"})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
