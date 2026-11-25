"""M07 formal report review and analyst-signature module.

The package exposes ports and a deterministic in-memory adapter so the module
can be developed before the shared PostgreSQL/FastAPI platform lands. Production
composition must supply the shared transaction, provider and qualification
adapters; fake providers are refused by the production composition root.
"""

from .domain import ActorContext, DomainError
from .automation import LocalDraftAssistant, ReportDraftAssistService
from .repository import InMemoryRepository
from .service import ReportSignatureService, ServiceResponse

__all__ = [
    "ActorContext",
    "DomainError",
    "InMemoryRepository",
    "LocalDraftAssistant",
    "ReportDraftAssistService",
    "ReportSignatureService",
    "ServiceResponse",
]
