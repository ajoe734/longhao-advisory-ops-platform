from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .domain import (
    ActorContext,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
    canonical_hash,
    new_id,
    require_uuid,
    utc_now,
)
from .repository import InMemoryRepository, StoreState


@dataclass(frozen=True)
class FieldExtractionSuggestion:
    document_id: str
    question_id: str
    ocr_value: str
    confidence: str  # decimal string, 0..1
    conflicts_with_customer_answer: bool


@dataclass(frozen=True)
class OcrExtractionRun:
    id: str
    tenant_id: str
    submission_id: str
    document_id: str
    model_version: str
    prompt_version: str
    ruleset_version: str
    source_refs: tuple[str, ...]
    input_sha256: str
    suggestions: tuple[FieldExtractionSuggestion, ...]
    created_at: datetime
    status: str = "untrusted_draft"


@dataclass(frozen=True)
class OcrExtractionReview:
    id: str
    tenant_id: str
    run_id: str
    reviewer_id: str
    question_id: str
    action: str  # confirmed_customer_answer | adopted_ocr_value | escalated
    note: str


class OcrAssistantPort(Protocol):
    is_fake: bool
    model_version: str
    prompt_version: str
    ruleset_version: str

    def extract(self, document_id: str, declared_answers: dict[str, dict[str, Any]]) -> tuple[FieldExtractionSuggestion, ...]: ...


class LocalOcrAssistant:
    """Deterministic local fallback; no document bytes ever leave this module."""

    is_fake = True
    model_version = "local-rules-no-llm-v1"
    prompt_version = "m03-ocr-field-extraction-v1"
    ruleset_version = "m03-ocr-conflict-detection-v1"

    def __init__(self) -> None:
        self._registered: dict[str, dict[str, str]] = {}

    def register(self, document_id: str, extracted: dict[str, str]) -> None:
        """Synthetic registration standing in for an OCR vendor response."""
        self._registered[document_id] = dict(extracted)

    def extract(self, document_id: str, declared_answers: dict[str, dict[str, Any]]) -> tuple[FieldExtractionSuggestion, ...]:
        extracted = self._registered.get(document_id, {})
        suggestions: list[FieldExtractionSuggestion] = []
        for question_id, ocr_value in extracted.items():
            declared = declared_answers.get(question_id)
            declared_value = declared["value"] if declared and declared["answer_type"] == "text" else None
            suggestions.append(
                FieldExtractionSuggestion(
                    document_id=document_id,
                    question_id=question_id,
                    ocr_value=ocr_value,
                    confidence="0.900000",
                    conflicts_with_customer_answer=declared_value is not None and declared_value != ocr_value,
                )
            )
        return tuple(suggestions)


class OcrAutomationService:
    """Extracts untrusted field drafts; never overwrites the customer's answer."""

    def __init__(self, repository: InMemoryRepository, assistant: OcrAssistantPort, *, environment: str = "test") -> None:
        if environment == "production" and getattr(assistant, "is_fake", False):
            raise RuntimeError("fake M03 OCR adapter is forbidden in production")
        self.repository = repository
        self.assistant = assistant

    def extract(self, context: ActorContext, submission_id: str, document_id: str) -> OcrExtractionRun:
        if context.principal_type != "staff" or not context.roles & {"kyc_reviewer"}:
            raise ForbiddenError("actor cannot request OCR extraction")
        submission_id = require_uuid(submission_id, "submission_id")
        document_id = require_uuid(document_id, "document_id")

        def save(state: StoreState) -> OcrExtractionRun:
            submission = state.submissions.get((context.tenant_id, submission_id))
            if not submission:
                raise NotFoundError("kyc submission was not found")
            document = state.documents.get((context.tenant_id, document_id))
            owning_submission = state.submissions.get((context.tenant_id, document.submission_id)) if document else None
            if (
                not document or not owning_submission
                or owning_submission.application_id != submission.application_id
                or document.customer_id != submission.customer_id
            ):
                raise ValidationError("document does not belong to this submission", field_name="document_id")
            if document.scan_status != "clean":
                raise ConflictError("document has not passed the quarantine scan", code="document_not_clean")
            suggestions = self.assistant.extract(document_id, submission.answers)
            run = OcrExtractionRun(
                id=new_id(),
                tenant_id=context.tenant_id,
                submission_id=submission_id,
                document_id=document_id,
                model_version=self.assistant.model_version,
                prompt_version=self.assistant.prompt_version,
                ruleset_version=self.assistant.ruleset_version,
                source_refs=(document.id,),
                input_sha256=canonical_hash({"document_id": document_id, "answers": submission.answers}),
                suggestions=suggestions,
                created_at=utc_now(),
            )
            state.automation_runs[(context.tenant_id, run.id)] = run
            return run

        return self.repository.atomic(save)

    def record_review(
        self,
        context: ActorContext,
        run_id: str,
        *,
        question_id: str,
        action: str,
        note: str,
    ) -> OcrExtractionReview:
        if context.principal_type != "staff" or not context.roles & {"kyc_reviewer"}:
            raise ForbiddenError("actor cannot review OCR extraction suggestions")
        run_id = require_uuid(run_id, "run_id")
        if action not in {"confirmed_customer_answer", "adopted_ocr_value", "escalated"}:
            raise ValidationError("review action is not supported", field_name="action")
        if not 1 <= len(note) <= 300:
            raise ValidationError("note must contain 1 to 300 characters", field_name="note")
        review = OcrExtractionReview(
            id=new_id(), tenant_id=context.tenant_id, run_id=run_id, reviewer_id=context.actor_id,
            question_id=question_id, action=action, note=note,
        )

        def save(state: StoreState) -> OcrExtractionReview:
            if (context.tenant_id, run_id) not in state.automation_runs:
                raise ValidationError("OCR extraction run was not found", code="automation_run_not_found")
            state.automation_reviews[(context.tenant_id, review.id)] = review
            return review

        return self.repository.atomic(save)
