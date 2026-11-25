"""M05 Pantheon research candidate intake module.

The package exposes ports and a deterministic in-memory adapter so the module
can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction, signature and
evidence-source adapters; fake providers are refused by the production
composition root.
"""

from .adapter import PantheonAdapter
from .automation import CandidateTriageAutomationService, LocalTriageAssistant
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import PantheonIntakeService, ServiceResponse

__all__ = [
    "ActorContext",
    "CandidateTriageAutomationService",
    "DomainError",
    "InMemoryRepository",
    "LocalTriageAssistant",
    "PantheonAdapter",
    "PantheonIntakeService",
    "ServiceResponse",
]
