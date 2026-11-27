"""Fixed synthetic identities for local/development/test only.

Every identity is an exact ``Authorization`` marker bound to the fixed
synthetic tenant; nothing is decoded from client-supplied text. Production
never uses ``SyntheticAuthenticator`` (``PlatformRuntime.bootstrap`` requires a
real authenticator there), so these markers grant nothing outside development.

Role codes are the ones the module services actually check (see
docs/11-security-rbac.md). Roles that take part in maker/checker or
"different person" rules get two distinct identities so the separation can be
exercised through HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from longhao_platform.types import Principal

SYNTHETIC_TENANT_ID = UUID("3e624789-e990-5c6b-bf39-82de2afce2c8")
SYNTHETIC_ADMIN_ID = UUID("7dc75c5a-fcc2-5ace-b905-fd5d6405d840")
SYNTHETIC_WORKER_ID = UUID("3e8b6b56-35ef-5bf8-9cb7-ef680b2f76c2")

# Namespace for deterministic persona ids (uuid5 of "synthetic:<key>").
_PERSONA_NAMESPACE = UUID("6f0d8f4e-2b8a-5c1e-9d1f-6c6f6e67686f")
_MARKER_PREFIX = "Bearer synthetic-"


@dataclass(frozen=True, slots=True)
class SyntheticPersona:
    key: str
    label: str
    group: str
    roles: frozenset[str]
    actor_type: str = "human"
    actor_id: UUID | None = None
    subject: str | None = None

    @property
    def id(self) -> UUID:
        return self.actor_id or uuid5(_PERSONA_NAMESPACE, f"synthetic:{self.key}")

    @property
    def authorization(self) -> str:
        return f"{_MARKER_PREFIX}{self.key}"

    def principal(self) -> Principal:
        return Principal(
            self.id,
            SYNTHETIC_TENANT_ID,
            self.subject or f"{self.key}@synthetic.invalid",
            self.roles,
            actor_type=self.actor_type,
        )

    def public_view(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "roles": sorted(self.roles),
            "actor_type": self.actor_type,
            "actor_id": str(self.id),
            "authorization": self.authorization,
        }


def _p(
    key: str,
    label: str,
    group: str,
    *roles: str,
    actor_type: str = "human",
    actor_id: UUID | None = None,
    subject: str | None = None,
) -> SyntheticPersona:
    return SyntheticPersona(key, label, group, frozenset(roles), actor_type, actor_id, subject)


PERSONAS: tuple[SyntheticPersona, ...] = (
    # The two original markers keep their ids and roles.
    _p("platform-admin", "平台管理員", "平台", "platform_admin", "internal_auditor",
       actor_id=SYNTHETIC_ADMIN_ID, subject="platform-admin@synthetic.invalid"),
    _p("worker", "平台背景作業", "平台", "platform_worker", actor_type="service",
       actor_id=SYNTHETIC_WORKER_ID, subject="platform-worker@synthetic.invalid"),
    # 客戶與契約（M01-M04）
    _p("cms-operator", "客戶作業員", "客戶與契約", "cms_operator"),
    _p("cms-supervisor", "客戶作業主管", "客戶與契約", "cms_supervisor"),
    _p("contract-operator", "契約作業員", "客戶與契約", "contract_operator"),
    _p("contract-supervisor", "契約主管", "客戶與契約", "contract_supervisor"),
    _p("company-acceptor", "公司承諾核決人", "客戶與契約", "company_acceptor"),
    _p("kyc-reviewer", "KYC 審查員", "客戶與契約", "kyc_reviewer"),
    _p("suitability-reviewer", "適合度審查員", "客戶與契約", "suitability_reviewer"),
    _p("suitability-authorizer", "適合度核決人", "客戶與契約", "suitability_authorizer"),
    _p("aml-officer", "AML 專員甲", "客戶與契約", "aml_officer"),
    _p("aml-officer-2", "AML 專員乙", "客戶與契約", "aml_officer"),
    _p("aml-supervisor", "AML 主管", "客戶與契約", "aml_supervisor"),
    # 研究與交付（M05-M09）
    _p("analyst", "分析師甲", "研究與交付", "analyst"),
    _p("analyst-2", "分析師乙", "研究與交付", "analyst"),
    _p("analyst-reviewer", "研究覆核員", "研究與交付", "analyst_reviewer"),
    _p("analyst-signer", "簽章分析師", "研究與交付", "analyst", "analyst_signer"),
    _p("research-supervisor", "研究主管", "研究與交付", "research_supervisor"),
    _p("compliance-reviewer", "法遵審查員", "研究與交付", "compliance_reviewer"),
    _p("compliance-supervisor", "法遵主管", "研究與交付", "compliance_supervisor"),
    _p("delivery-operator", "交付作業員", "研究與交付", "delivery_operator"),
    _p("delivery-supervisor", "交付主管", "研究與交付", "delivery_supervisor"),
    # 業務與歸屬（M10-M11）
    _p("sales", "業務甲", "業務與歸屬", "sales"),
    _p("sales-2", "業務乙", "業務與歸屬", "sales"),
    _p("sales-supervisor", "業務主管", "業務與歸屬", "sales_supervisor"),
    _p("marketing", "行銷", "業務與歸屬", "marketing"),
    _p("hr-operator", "人資作業員", "業務與歸屬", "hr_operator"),
    # 獎金與財會（M12、M14）
    _p("commission-operator", "獎金試算員", "獎金與財會", "commission_operator"),
    _p("commission-approver", "獎金核決人", "獎金與財會", "commission_approver"),
    _p("payroll-operator", "薪資作業員", "獎金與財會", "payroll_operator"),
    _p("finance-operator", "財務作業員（maker）", "獎金與財會", "finance_operator"),
    _p("finance-checker", "財務覆核員（checker）", "獎金與財會", "finance_checker"),
    _p("internal-auditor", "內部稽核", "獎金與財會", "internal_auditor"),
    # 客服（M13）
    _p("service-agent", "客服人員甲", "客服", "service_agent"),
    _p("service-agent-2", "客服人員乙", "客服", "service_agent"),
    _p("service-supervisor", "客服主管", "客服", "service_supervisor"),
    # 客戶（LegendFlow 會員）
    _p("member-a", "合成客戶甲", "客戶", "member", actor_type="customer"),
    _p("member-b", "合成客戶乙", "客戶", "member", actor_type="customer"),
    # 系統服務與 adapter（無人工決策權）
    _p("research-intake-service", "研究候選接收服務", "系統服務", "research_intake_service", actor_type="service"),
    _p("pantheon-adapter", "Pantheon adapter", "系統服務", "pantheon_adapter", actor_type="service"),
    _p("servant-adapter", "Servant adapter", "系統服務", "servant_adapter", actor_type="service"),
    _p("signature-adapter", "簽章 adapter", "系統服務", "signature_adapter", actor_type="service"),
    _p("aml-adapter", "AML 篩檢 adapter", "系統服務", "aml_adapter", actor_type="service"),
    _p("entitlement-service", "權限評估服務", "系統服務", "entitlement_service", actor_type="service"),
    _p("ownership-service", "歸屬政策服務", "系統服務", "ownership_service", actor_type="service"),
    _p("service-response-service", "客服回覆政策服務", "系統服務", "service_response_service", actor_type="service"),
    _p("service-messaging-adapter", "客服訊息 adapter", "系統服務", "service_messaging_adapter", actor_type="service"),
    _p("payment-adapter", "金流 adapter", "系統服務", "payment_adapter", actor_type="service"),
    _p("refund-service", "退款服務", "系統服務", "refund_service", actor_type="service"),
    _p("accounting-adapter", "會計 adapter", "系統服務", "accounting_adapter", actor_type="service"),
    _p("payroll-adapter", "薪資 adapter", "系統服務", "payroll_adapter", actor_type="service"),
)

PERSONAS_BY_AUTHORIZATION: dict[str, SyntheticPersona] = {p.authorization: p for p in PERSONAS}
PERSONAS_BY_KEY: dict[str, SyntheticPersona] = {p.key: p for p in PERSONAS}

if len(PERSONAS_BY_KEY) != len(PERSONAS) or len({p.id for p in PERSONAS}) != len(PERSONAS):
    raise RuntimeError("SYNTHETIC_PERSONA_DUPLICATE")
