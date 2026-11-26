"""M14 finance, reconciliation, refund and internal-control module.

The package deliberately exposes ports and a deterministic in-memory adapter
so the module can be developed before the shared PostgreSQL/FastAPI platform
lands. Production composition must supply the shared transaction, auth and
provider adapters; fake providers are refused by the production composition
root.
"""

from .domain import ActorContext, DomainError
from .automation import LocalReconciliationAssistant, ReconciliationNarrationService
from .repository import InMemoryRepository
from .service import FinanceControlService, ServiceResponse

__all__ = [
    "ActorContext",
    "DomainError",
    "FinanceControlService",
    "InMemoryRepository",
    "LocalReconciliationAssistant",
    "ReconciliationNarrationService",
    "ServiceResponse",
]
