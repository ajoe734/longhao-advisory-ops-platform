#!/usr/bin/env python3
"""Local, synthetic contract mock. It is deliberately not an application backend."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "api/openapi.yaml").read_text())
CATALOG = json.loads((ROOT / "api/operation-catalog.json").read_text())
FIXTURES = json.loads((ROOT / "mocks/fixtures.json").read_text())
ROUTES = [(op, re.compile("^/api/v1" + re.sub(r"\{[^}]+\}", r"([^/]+)", op["path"]) + "$")) for op in CATALOG]
HISTORY = {}
HISTORY_LOCK = threading.Lock()
SCENARIOS = {
    "forbidden": (403, "AUTHORIZATION_DENIED", False),
    "conflict": (412, "STALE_AGGREGATE_VERSION", False),
    "invalid_transition": (409, "INVALID_STATE_TRANSITION", False),
    "policy_unapproved": (409, "POLICY_NOT_APPROVED", False),
    "dependency_unavailable": (503, "DEPENDENCY_UNAVAILABLE", True),
    "ambiguous_timeout": (503, "DEPENDENCY_OUTCOME_UNKNOWN_QUERY_FIRST", False),
    "rate_limited": (429, "RATE_LIMITED", True),
    "expired": (409, "CONTENT_EXPIRED", False),
    "entitlement_denied": (403, "ENTITLEMENT_NOT_ACTIVE", False),
}


def validator(name):
    return Draft202012Validator({"$ref": "#/components/schemas/" + name, "components": SPEC["components"]},
                                format_checker=FormatChecker())


class Handler(BaseHTTPRequestHandler):
    server_version = "LongGoodSyntheticContractMock/1.0"

    def log_message(self, *_args):
        # No request body, URL query, customer ID, credential or signed URL logging.
        pass

    def respond(self, status, body, replayed=False):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/problem+json" if status >= 400 else "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Contract-Mock", "synthetic-only")
        self.send_header("X-Correlation-Id", str(uuid4()))
        self.send_header("ETag", '"1"')
        self.send_header("Idempotency-Replayed", "true" if replayed else "false")
        if status in (429, 503):
            self.send_header("Retry-After", "2")
        self.end_headers()
        self.wfile.write(raw)

    def problem(self, status, code, detail="", retryable=False):
        self.respond(status, {"type": "https://example.invalid/problems/" + code.lower(),
                              "title": code, "status": status, "code": code, "detail": detail,
                              "correlation_id": str(uuid4()), "retryable": retryable})

    def handle_operation(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            return self.respond(200, {"mode": "synthetic-contract-mock", "operations": len(CATALOG),
                                      "business_logic": False, "authentication": "mock-only"})
        if self.headers.get("X-Mock-Mode") != "synthetic":
            return self.problem(400, "SYNTHETIC_MODE_REQUIRED", "Send X-Mock-Mode: synthetic; do not use real data or tokens.")
        match = next(((op, pattern.match(url.path)) for op, pattern in ROUTES
                      if op["method"] == self.command.lower() and pattern.match(url.path)), None)
        if match is None:
            return self.problem(404, "ROUTE_NOT_FOUND")
        op, path_match = match
        try:
            for value in path_match.groups():
                UUID(value)
        except ValueError:
            return self.problem(422, "INVALID_UUID_PATH")
        if op["auth"] == "integration":
            # Intentional mock check; not the production HMAC signature verification implementation.
            for header in ("X-Webhook-Timestamp", "X-Webhook-Nonce", "X-Integration-Key-Id", "X-Webhook-Signature"):
                if not self.headers.get(header):
                    return self.problem(401, "MOCK_INTEGRATION_HEADERS_MISSING", header)
            if self.headers.get("X-Webhook-Signature") != "synthetic-valid-signature":
                return self.problem(401, "MOCK_SIGNATURE_REJECTED")
        elif self.headers.get("Authorization") not in (
            "Bearer synthetic-staff", "Bearer synthetic-customer", "Bearer synthetic-service"
        ):
            return self.problem(401, "SYNTHETIC_TOKEN_REQUIRED", "Only documented synthetic bearer markers are accepted.")
        mutation = op["method"] != "get"
        key = self.headers.get("Idempotency-Key", "")
        if mutation and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            return self.problem(428, "IDEMPOTENCY_KEY_REQUIRED")
        if op["compare_and_set"] and not self.headers.get("If-Match"):
            return self.problem(428, "IF_MATCH_REQUIRED")
        if op["compare_and_set"] and self.headers.get("If-Match") != '"1"':
            return self.problem(412, "STALE_AGGREGATE_VERSION", 'This fixture has ETag "1". No real state transition is implemented.')
        for name, values in parse_qs(url.query).items():
            if name not in {"limit", "cursor", "as_of", "state"}:
                return self.problem(400, "UNKNOWN_QUERY_PARAMETER", name)
            if name == "limit":
                try:
                    if not 1 <= int(values[0]) <= 200:
                        raise ValueError()
                except ValueError:
                    return self.problem(422, "INVALID_PAGE_LIMIT")
        body = None
        if mutation:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    return self.problem(400, "INVALID_BODY_LENGTH")
                body = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                return self.problem(400, "INVALID_JSON")
            errors = list(validator(op["request"]).iter_errors(body))
            if errors:
                return self.problem(422, "SCHEMA_VALIDATION_FAILED", "; ".join(e.message[:160] for e in errors[:3]))
        scenario = self.headers.get("X-Mock-Scenario", "success")
        if scenario in SCENARIOS:
            status, code, retryable = SCENARIOS[scenario]
            return self.problem(status, code, "Explicit synthetic failure scenario.", retryable)
        if scenario != "success":
            return self.problem(400, "UNKNOWN_MOCK_SCENARIO")
        fixture = copy.deepcopy(FIXTURES[op["operation_id"]])
        fingerprint = hashlib.sha256(json.dumps({"method": self.command, "path": url.path, "body": body}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        namespace = (op["operation_id"], self.headers.get("Authorization", "integration"), key)
        if mutation:
            with HISTORY_LOCK:
                previous = HISTORY.get(namespace)
                if previous:
                    if previous[0] != fingerprint:
                        return self.problem(409, "IDEMPOTENCY_PAYLOAD_CONFLICT")
                    return self.respond(previous[1], previous[2], replayed=True)
                HISTORY[namespace] = (fingerprint, fixture["response_status"], fixture["response"])
        return self.respond(fixture["response_status"], fixture["response"])

    do_GET = handle_operation
    do_POST = handle_operation
    do_PATCH = handle_operation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Synthetic contracts only: http://127.0.0.1:{args.port}; {len(CATALOG)} operations", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
