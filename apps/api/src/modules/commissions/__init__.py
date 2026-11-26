"""M12 commission policy, calculation, adjustment and payout module.

The package deliberately exposes ports and deterministic in-memory adapters so
the module can be developed before the shared PostgreSQL/FastAPI platform
lands. Production composition must supply the shared transaction, auth and
provider adapters; fake providers are refused by the production composition root.
"""

from .automation import CommissionExplainerService, LocalRuleExplainer
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import CommissionService, ServiceResponse

__all__ = [
    "ActorContext",
    "CommissionExplainerService",
    "CommissionService",
    "DomainError",
    "InMemoryRepository",
    "LocalRuleExplainer",
    "ServiceResponse",
]
