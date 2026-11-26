from __future__ import annotations

import pytest

from modules.ownership_pool.automation import HandoffAutomationService, LocalHandoffAssistant
from modules.ownership_pool.domain import ForbiddenError, ValidationError

from conftest import TENANT


EVIDENCE_A = "30000000-0000-0000-0000-000000000011"
EVIDENCE_B = "30000000-0000-0000-0000-000000000012"
PREDECESSOR = {"reason_code": "manual_release", "valid_from": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z"}


def _signals():
    return [
        {"signal_type": "contact_attempt", "occurred_at": "2026-10-05T00:00:00Z", "evidence_asset_id": EVIDENCE_A},
        {"signal_type": "contact_attempt", "occurred_at": "2026-10-20T00:00:00Z", "evidence_asset_id": EVIDENCE_A},
        {"signal_type": "complaint", "occurred_at": "2026-10-10T00:00:00Z", "evidence_asset_id": EVIDENCE_B},
    ]


def test_handoff_summary_is_untrusted_draft_with_versions_and_source_refs(m11, sales_a):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    run = automation.summarize(sales_a, PREDECESSOR, _signals())
    assert run.status == "untrusted_draft"
    assert run.model_version == "local-rules-no-llm-v1"
    assert run.prompt_version and run.ruleset_version
    assert set(run.source_refs) == {EVIDENCE_A, EVIDENCE_B}
    assert "manual_release" in run.suggestion.summary
    assert run.suggestion.contact_points[0].startswith("contact_attempt")  # most frequent signal first
    snapshot = m11["repository"].snapshot()
    assert snapshot.automation_runs[(TENANT, run.id)] == run


def test_handoff_review_requires_adopted_summary_only_when_modified(m11, sales_a):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    run = automation.summarize(sales_a, PREDECESSOR, _signals())

    adopted = automation.record_review(sales_a, run.id, action="adopted", reason_code="matches_known_history")
    assert adopted.action == "adopted"
    assert adopted.adopted_summary is None

    with pytest.raises(ValidationError) as excinfo:
        automation.record_review(sales_a, run.id, action="modified", reason_code="needs_correction")
    assert excinfo.value.field_name == "adopted_summary"

    modified = automation.record_review(
        sales_a, run.id, action="modified", reason_code="needs_correction", adopted_summary="人工修正後的摘要",
    )
    assert modified.adopted_summary == "人工修正後的摘要"
    snapshot = m11["repository"].snapshot()
    assert len(snapshot.automation_reviews) == 2


def test_handoff_review_for_unknown_run_is_rejected(m11, sales_a):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    with pytest.raises(ValidationError) as excinfo:
        automation.record_review(
            sales_a, "00000000-0000-0000-0000-000000000099", action="adopted", reason_code="n/a",
        )
    assert excinfo.value.code == "automation_run_not_found"


def test_handoff_automation_rejects_non_minimized_input(m11, sales_a):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    with pytest.raises(ValidationError) as excinfo:
        automation.summarize(sales_a, PREDECESSOR, [{**_signals()[0], "transcript": "raw PII text"}])
    assert excinfo.value.code == "automation_input_not_minimized"


def test_handoff_automation_requires_sales_role(m11, hr_operator):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    with pytest.raises(ForbiddenError):
        automation.summarize(hr_operator, PREDECESSOR, _signals())


def test_handoff_automation_never_touches_ownership_state(m11, sales_a):
    automation = HandoffAutomationService(m11["repository"], LocalHandoffAssistant())
    before = m11["repository"].snapshot()
    automation.summarize(sales_a, PREDECESSOR, _signals())
    after = m11["repository"].snapshot()
    # The automation service has no reference to OwnershipPoolService and no
    # write path into any of these collections — asserting they are untouched
    # is a regression guard against that boundary ever being crossed.
    assert after.assignments == before.assignments
    assert after.pool_entries == before.pool_entries
    assert after.departures == before.departures
    assert after.policies == before.policies


def test_production_handoff_automation_refuses_local_fake(m11):
    with pytest.raises(RuntimeError):
        HandoffAutomationService(m11["repository"], LocalHandoffAssistant(), environment="production")
