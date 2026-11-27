// UI-01..UI-34 -> the container that loads the module data and renders its feature components.
import type { Screen } from "./common";
import {
  AcceptanceScreen, AmlCaseScreen, AmlQueueScreen, ApplicationScreen, ContractScreen, Customer360Screen,
  CustomerSearchScreen, EntitlementsScreen, KycScreen, SuitabilityScreen,
} from "./onboarding";
import {
  CandidateDetailScreen, CandidateQueueScreen, DeliveriesScreen, PublicationScreen, ReportVersionsScreen,
  ReviewQueueScreen, ReviewWorkspaceScreen, SignScreen,
} from "./research";
import {
  AccessScreen, AuditEvidenceScreen, CloseScreen, CommissionEntryScreen, CommissionsScreen, CompanyPoolScreen,
  FunnelScreen, OpportunityScreen, OperationsScreen, OwnershipTransfersScreen, ReconciliationScreen, RefundScreen,
  RevenueScreen, SupportCaseScreen, SupportQueueScreen, WorklistScreen,
} from "./operations";

export type { Screen, ScreenProps } from "./common";

export const SCREENS: Record<string, Screen> = {
  "UI-01": CustomerSearchScreen,
  "UI-02": Customer360Screen,
  "UI-03": ApplicationScreen,
  "UI-04": AcceptanceScreen,
  "UI-05": ContractScreen,
  "UI-06": EntitlementsScreen,
  "UI-07": KycScreen,
  "UI-08": SuitabilityScreen,
  "UI-09": AmlQueueScreen,
  "UI-10": AmlCaseScreen,
  "UI-11": CandidateQueueScreen,
  "UI-12": CandidateDetailScreen,
  "UI-13": ReviewQueueScreen,
  "UI-14": ReviewWorkspaceScreen,
  "UI-15": ReportVersionsScreen,
  "UI-16": SignScreen,
  "UI-17": PublicationScreen,
  "UI-18": DeliveriesScreen,
  "UI-19": WorklistScreen,
  "UI-20": OpportunityScreen,
  "UI-21": FunnelScreen,
  "UI-22": CompanyPoolScreen,
  "UI-23": OwnershipTransfersScreen,
  "UI-24": CommissionsScreen,
  "UI-25": CommissionEntryScreen,
  "UI-26": SupportQueueScreen,
  "UI-27": SupportCaseScreen,
  "UI-28": ReconciliationScreen,
  "UI-29": RefundScreen,
  "UI-30": RevenueScreen,
  "UI-31": CloseScreen,
  "UI-32": AuditEvidenceScreen,
  "UI-33": AccessScreen,
  "UI-34": OperationsScreen,
};
