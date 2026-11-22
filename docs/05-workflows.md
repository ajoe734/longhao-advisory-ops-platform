# Workflows and State Machines

## Application
draft -> customer_signed -> due_diligence_pending -> review_in_progress -> accepted/rejected/withdrawn.

## Contract
pending_company_acceptance -> accepted -> effective -> suspended/expired/terminated.

## Payment
pending -> authorized/paid/failed/refund_pending/refunded/disputed.

## Entitlement
locked -> eligible -> active -> suspended/expired/revoked.

These are separate aggregates.

## Due diligence
KYC: not_started -> in_progress -> submitted -> incomplete/under_review/approved/rejected/refresh_required.
Suitability: pending -> assessed -> suitable/conditional/not_suitable/reassessment_due.
AML/CFT: pending_screening -> clear/possible_match/confirmed_match/escalated/closed. Restricted details only to authorized AML roles. No LLM auto-clear.

## Research
candidate received -> validation -> review queue -> analyst review -> revision requested/revised -> professional review -> compliance review (if required) -> signature pending -> signature verified -> publication -> channel dispatch. Rejected/withdrawn candidates remain archived. New candidate during review marks current review stale. Signed version immutable.

## CRM and Company Pool
Funnel: new -> contacted -> qualified -> application_started -> customer_signed -> due_diligence -> accepted -> paid -> active -> renewal/upgrade/churn.
Maintenance assignment has effective dates and reason. Seven consecutive days without qualifying maintenance activity returns eligible relationship to Company Pool; employee departure triggers immediate transfer. Active contracts may remain active. Complaints, refund, AML restriction and legal hold are separate exceptions/cases.

## Commission
Basis: finance-confirmed actual untaxed service revenue net of attributable refunds. Marketing coefficients: year 1=100%, year 2=50%, year 3 onward=25% for qualifying continuous auto-renewal. Sales-led renewal allocation marketing/sales=30/70. Base pool rate 3% proposal only, not approved. Keep source, maintenance owner and conversion owner separate. Upgrade incremental service calculated separately. Refund creates linked adjustment.

## Service case
new -> triaged -> assigned -> waiting_customer/waiting_internal -> resolved -> closed/reopened. Case owner differs from maintenance owner.

## Refund
requested -> eligibility_check -> calculation_prepared -> approval_pending -> approved -> provider_submitted -> confirmed/failed/reconciliation_required -> accounting_adjusted -> closed. Timeout requires status lookup before retry.
