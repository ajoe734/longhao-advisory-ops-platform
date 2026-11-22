#!/usr/bin/env python3
"""Validate the full engineering contract set; no network or production credentials."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate as validate_openapi

ROOT = Path(__file__).resolve().parents[1]


def json_read(path: Path):
    return json.loads(path.read_text())


def schema_validator(spec: dict, schema_name: str):
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema_name}", "components": spec["components"]},
        format_checker=FormatChecker(),
    )


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def walk_refs(node, spec):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            require(ref.startswith("#/"), f"External/unpinned reference: {ref}")
            found = spec
            for token in ref[2:].split("/"):
                require(token in found, f"Missing ref: {ref}")
                found = found[token]
        for value in node.values():
            walk_refs(value, spec)
    elif isinstance(node, list):
        for value in node:
            walk_refs(value, spec)


def main():
    spec = yaml.safe_load((ROOT / "api/openapi.yaml").read_text())
    catalog = json_read(ROOT / "api/operation-catalog.json")
    fixtures = json_read(ROOT / "mocks/fixtures.json")
    registry = json_read(ROOT / "events/registry.json")
    walk_refs(spec, spec)
    validate_openapi(spec)
    require(spec["openapi"].startswith("3.1."), "Use OpenAPI 3.1")
    expected_modules = {f"M{i:02}" for i in range(1, 15)}
    require({op["module"] for op in catalog} == expected_modules, "All 14 modules must be covered")
    operation_ids = [op["operation_id"] for op in catalog]
    require(len(operation_ids) == len(set(operation_ids)), "Duplicate operationId")
    require(set(operation_ids) == set(fixtures), "Every operation must have an isolated fixture")
    positives = negatives = 0
    for op in catalog:
        operation = spec["paths"][op["path"]][op["method"]]
        require(operation["operationId"] == op["operation_id"], "Catalog path drift")
        require(operation.get("security"), f"Unauthenticated operation {op['operation_id']}")
        require(operation.get("x-allowed-roles"), f"Missing role scope {op['operation_id']}")
        parameters = [p.get("$ref", "") for p in operation["parameters"]]
        if op["method"] != "get":
            require("#/components/parameters/IdempotencyKey" in parameters, "Mutation missing replay protection")
        if op["compare_and_set"]:
            require("#/components/parameters/IfMatch" in parameters, "Mutation missing CAS")
        fixture = fixtures[op["operation_id"]]
        for side in ("request", "response"):
            name = op[side]
            if name is None:
                continue
            validator = schema_validator(spec, name)
            errors = list(validator.iter_errors(fixture[side]))
            require(not errors, f"{op['operation_id']} {side}: " + "; ".join(e.message[:200] for e in errors[:3]))
            positives += 1
            if side == "request":
                bad = copy.deepcopy(fixture[side])
                bad["undeclared_admin_override"] = True
                require(not validator.is_valid(bad), f"Unknown field accepted by {name}")
                negatives += 1
                required = spec["components"]["schemas"][name].get("required", [])
                if required:
                    bad = copy.deepcopy(fixture[side])
                    bad.pop(required[0], None)
                    require(not validator.is_valid(bad), f"Missing mandatory field accepted by {name}")
                    negatives += 1
    # Company may decline or request information before all CDD records exist,
    # while acceptance must bind each approved due-diligence reference.
    accept = copy.deepcopy(fixtures["decide_company_acceptance"]["request"])
    partial = {k: accept[k] for k in ["package_version_id", "customer_signature_id", "reason_code"]}
    partial["decision"] = "request_changes"
    acceptance_validator = schema_validator(spec, "CompanyAcceptanceRequest")
    require(acceptance_validator.is_valid(partial), "Information requests must not require invented approved CDD records")
    positives += 1
    partial["decision"] = "accept"
    require(not acceptance_validator.is_valid(partial), "Acceptance missing due diligence references must be rejected")
    negatives += 1
    # Specific safety-sensitive contract boundaries, not merely mirrored field shapes.
    money = schema_validator(spec, "Money")
    require(not money.is_valid({"amount": 100.1, "currency": "TWD"}), "Monetary floats must be rejected")
    negatives += 1
    for schema_name, discriminator, success in [
        ("CustomerSignatureCallback", "result", "signed"),
        ("AnalystSignatureCallback", "verification_result", "verified"),
    ]:
        op = next(o for o in catalog if o["request"] == schema_name)
        body = copy.deepcopy(fixtures[op["operation_id"]]["request"])
        body[discriminator] = success
        body.pop("signed_file_sha256", None)
        require(not schema_validator(spec, schema_name).is_valid(body), "Verified signatures must bind signed-file hash")
        negatives += 1
    require(not any(op["method"] == "post" and op["path"] == "/publications" for op in catalog),
            "No human second-Publish command: verified signature drives system publication")
    event_types = set()
    for item in registry:
        require(item["event_type"] not in event_types, "Duplicate event type")
        event_types.add(item["event_type"])
        require(set(item["consumers"]) <= expected_modules, "Unknown event consumer")
        require(item["producer"] in expected_modules, "Unknown event producer")
        schema = json_read(ROOT / "events" / item["schema"])
        example = json_read(ROOT / "events" / item["example"])
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(example))
        require(not errors, f"Event fixture {item['event_type']}: " + "; ".join(e.message[:200] for e in errors[:3]))
        positives += 1
        checksum = hashlib.sha256(json.dumps(example["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        require(checksum == example["payload_sha256"], "Fixture payload hash mismatch")
        aggregate_keys = {"kyc_submission":"submission_id", "suitability_assessment":"assessment_id", "aml_case":"case_id", "business_clearance":"clearance_id", "research_candidate":"candidate_id", "maintenance_assignment":"assignment_id", "commission_run":"run_id", "commission_adjustment":"adjustment_id", "service_case":"case_id", "revenue_recognition":"recognition_id", "accounting_export":"export_id", "service_response":"response_id"}
        aggregate_key = aggregate_keys.get(example["aggregate_type"], example["aggregate_type"] + "_id")
        if aggregate_key in example["payload"]:
            require(example["aggregate_id"] == example["payload"][aggregate_key], "Fixture aggregate ID disagrees with payload authority")
        if item["event_type"] == "report.signature_verified":
            payload = example["payload"]
            require(datetime.fromisoformat(payload["valid_until"]) > datetime.fromisoformat(payload["verified_at"]), "Signature fixture is already expired at verification")
            require(len({payload["report_content_sha256"], payload["bundle_manifest_sha256"], payload["signed_file_sha256"]}) == 3, "Fixtures must distinguish the three signature hashes")
        for bad in [dict(example, schema_version="99.0.0"), dict(example, tenant_id="not-a-uuid")]:
            require(not validator.is_valid(bad), "Event version/tenant validation missing")
            negatives += 1
        bad = copy.deepcopy(example)
        bad["payload"]["aml_investigation_details"] = "synthetic forbidden field"
        require(not validator.is_valid(bad), "Raw AML payload injection must be rejected")
        negatives += 1
    verified = next(e for e in registry if e["event_type"] == "report.signature_verified")
    require(verified["producer"] == "M07" and verified["consumers"] == ["M08"], "Signature publication authority drift")
    ports = yaml.safe_load((ROOT / "api/adapter-ports.yaml").read_text())
    for name, port in ports["ports"].items():
        require(set(port.get("operations", [])) <= set(operation_ids), f"Adapter {name} references unknown operation")
        require(set(port.get("input_events", [])) <= event_types, f"Adapter {name} references unknown event")
        if "output_event" in port:
            require(port["output_event"] in event_types, f"Adapter {name} output event is undefined")
    require(ports["rules"]["external_repository_mutation"] == "prohibited", "External repo boundary drift")
    require(ports["rules"]["development_blocked_by_unavailable_provider"] is False, "Parallel development must not wait for external provider")
    print(json.dumps({
        "status": "passed", "modules": len(expected_modules), "operations": len(catalog),
        "schemas": len(spec["components"]["schemas"]), "event_contracts": len(registry),
        "positive_schema_cases": positives, "negative_schema_cases": negatives,
        "note": "Contract validation only; does not certify application behavior, vendors, security or legal readiness."
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CONTRACT VALIDATION FAILED: {type(exc).__name__}: {str(exc)[:1600]}", file=sys.stderr)
        sys.exit(1)
