"""M06 Agora research review workbench and Servant collaboration module.

The package exposes ports and a deterministic in-memory adapter so the
module can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction and Agora/Servant
adapters; fake providers are refused by the production composition root.
"""

from .automation import AgoraDraftAutomationService, LocalDraftAssistant
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import AgoraReviewService, ServiceResponse

__all__ = [
    "ActorContext",
    "AgoraDraftAutomationService",
    "AgoraReviewService",
    "DomainError",
    "InMemoryRepository",
    "LocalDraftAssistant",
    "ServiceResponse",
]
