#!/usr/bin/env python3
"""Rebuild versioned OpenAPI/events/synthetic fixtures; no external systems are accessed."""
from pathlib import Path
import json, yaml, re, uuid
ROOT=Path(__file__).resolve().parents[1]
OPS=[]
def op(module,method,path,oid,req,res,summary,roles,auth='staff',status=200,cas=False,event=None):
    OPS.append(dict(module=module,method=method,path=path,operation_id=oid,request=req,response=res,summary=summary,roles=roles.split(','),auth=auth,status=status,compare_and_set=cas,emits=event))
# M01
op('M01','post','/customers','create_customer','CustomerCreate','Customer','建立或解析單一客戶主檔','cms_operator','customer',201)
op('M01','get','/customers','list_customers',None,'CustomerPage','依授權範圍查詢客戶','cms_operator,sales,service_agent')
op('M01','get','/customers/{customer_id}','get_customer',None,'Customer','取得客戶總覽及遮罩資料','cms_operator,sales,service_agent')
op('M01','post','/customers/{customer_id}/identity-links','link_external_identity','IdentityLinkRequest','ExternalIdentity','綁定已驗證外部身分','cms_operator','customer',201,True)
op('M01','post','/customer-merges','propose_customer_merge','CustomerMergeRequest','MergeCase','提出重複主檔合併覆核','cms_operator',status=201)
op('M01','post','/customer-merges/{merge_case_id}/decision','decide_customer_merge','Decision','MergeCase','由不同人覆核身分合併','cms_supervisor',cas=True)
op('M01','post','/applications','create_application','ApplicationCreate','Application','建立申辦案件，保留行銷來源','cms_operator','customer',201)
op('M01','get','/applications/{application_id}','get_application',None,'Application','取得申辦進度與允許下一動作','cms_operator','customer')
op('M01','post','/applications/{application_id}/withdrawal','withdraw_application','ReasonRequest','Application','客戶撤回申請並啟動款項處理','cms_operator','customer',cas=True)
op('M01','post','/customers/{customer_id}/consents','record_consent','ConsentRequest','Consent','新增版次化同意證據','cms_operator','customer',201,True)
# M02
op('M02','post','/contract-packages','create_contract_package','PackageCreate','ContractPackage','建立契約文件版本，待法遵核准','contract_operator',status=201)
op('M02','post','/contract-packages/{package_version_id}/approval','approve_contract_package','Decision','ContractPackage','核准可供簽署的固定文件包','compliance_reviewer',cas=True)
op('M02','get','/contract-packages/{package_version_id}','get_contract_package',None,'ContractPackage','取得指定契約文件與版本','contract_operator','customer')
op('M02','post','/applications/{application_id}/customer-signature','create_customer_signature','CustomerSignatureRequest','SignatureSession','客戶先簽署申辦／契約文件包','contract_operator','customer',202,True)
op('M02','post','/webhooks/customer-signatures','receive_customer_signature','CustomerSignatureCallback','CallbackReceipt','驗證客戶簽署完成回呼','signature_adapter','integration',202)
op('M02','post','/applications/{application_id}/company-acceptance','decide_company_acceptance','CompanyAcceptanceRequest','CompanyAcceptance','KYC、適合度與 AML clearance 完成後記錄公司承諾','company_acceptor',cas=True)
op('M02','get','/contracts/{contract_id}','get_contract',None,'Contract','查看契約成立、生效與服務期間','contract_operator','customer')
op('M02','post','/contracts/{contract_id}/amendments','create_contract_amendment','AmendmentRequest','Application','升級或變更形成新申辦與明確差額','contract_operator','customer',201,True)
op('M02','post','/contracts/{contract_id}/termination','terminate_contract','TerminationRequest','Contract','記錄契約終止及退款評估','contract_operator',cas=True)
op('M02','get','/customers/{customer_id}/entitlements','list_customer_entitlements',None,'EntitlementPage','列示服務權限及有效區間','contract_operator','customer')
op('M02','post','/contracts/{contract_id}/entitlement-evaluations','evaluate_entitlements','EntitlementEvaluationRequest','EntitlementDecision','授權系統重算條件而非任意開通','entitlement_service','service',202,True)
op('M02','post','/entitlements/{entitlement_id}/suspension','suspend_entitlement','SuspensionRequest','Entitlement','有依據的服務權限暫停','contract_supervisor',cas=True)
# M03
op('M03','post','/applications/{application_id}/kyc-submissions','create_kyc_submission','KycDraftRequest','KycSubmission','簽署後建立線上 KYC 資料草稿','kyc_reviewer','customer',201,True)
op('M03','post','/kyc-submissions/{submission_id}/document-uploads','request_kyc_upload','DocumentUploadRequest','UploadTicket','取得隔離區上傳票證與檔案掃描狀態','kyc_reviewer','customer',201,True)
op('M03','post','/kyc-submissions/{submission_id}/submit','submit_kyc','KycSubmitRequest','KycSubmission','提交問卷原始回答與證明文件','kyc_reviewer','customer',202,True)
op('M03','get','/kyc-submissions/{submission_id}','get_kyc_submission',None,'KycSubmission','依受限權限取得 KYC 案件','kyc_reviewer')
op('M03','post','/kyc-submissions/{submission_id}/decision','decide_kyc','KycDecisionRequest','KycSubmission','完成核驗或要求補件','kyc_reviewer',cas=True)
op('M03','post','/applications/{application_id}/suitability-assessments','assess_suitability','SuitabilityRequest','SuitabilityAssessment','適合度評估使用固定問卷與規則版本','suitability_reviewer',status=201,cas=True)
op('M03','post','/customers/{customer_id}/due-diligence-refreshes','request_due_diligence_refresh','RefreshRequest','KycSubmission','依到期或風險事件建立重評案件','kyc_reviewer',status=201,cas=True)
# M04
op('M04','post','/applications/{application_id}/aml-declarations','submit_aml_declaration','AmlDeclarationRequest','AmlDeclarationReceipt','客戶線上聲明與證明資料，並非 AML 放行','aml_officer','customer',201,True)
op('M04','post','/aml/screenings','start_aml_screening','ScreeningRequest','AmlScreening','發起身分／名單篩檢','aml_officer',status=202)
op('M04','post','/webhooks/aml-screenings','receive_aml_screening','ScreeningCallback','CallbackReceipt','接收受限區名單比對結果','aml_adapter','integration',202)
op('M04','get','/aml/cases','list_aml_cases',None,'AmlCasePage','受限 AML 案件工作佇列','aml_officer,aml_supervisor')
op('M04','get','/aml/cases/{aml_case_id}','get_aml_case',None,'AmlCase','檢視 AML 證據與審查紀錄','aml_officer,aml_supervisor')
op('M04','post','/aml/cases/{aml_case_id}/actions','add_aml_case_action','AmlActionRequest','AmlCase','要求 EDD、補件或向主管升級','aml_officer',cas=True)
op('M04','post','/aml/cases/{aml_case_id}/decision','decide_aml_case','AmlDecisionRequest','BusinessClearance','受權人裁決與公司建立關係之 clearance','aml_supervisor',cas=True)
op('M04','get','/applications/{application_id}/business-clearance','get_business_clearance',None,'BusinessClearance','僅輸出通過／待處理／不通過及有效期限','company_acceptor')
# M05
op('M05','post','/research-candidates','ingest_research_candidate','CandidateCreate','ResearchCandidate','接收 Pantheon Persona 或分析師來源候選研究','pantheon_adapter','integration',202)
op('M05','get','/research-candidates','list_research_candidates',None,'ResearchCandidatePage','候選研究接收與錯誤佇列','analyst,analyst_reviewer')
op('M05','get','/research-candidates/{candidate_id}','get_research_candidate',None,'ResearchCandidate','查詢來源版本、證據與 checksum','analyst,analyst_reviewer')
op('M05','post','/research-candidates/{candidate_id}/validation','validate_research_candidate','CandidateValidationRequest','ResearchCandidate','驗證雜湊、版次、授權範圍與來源引用','research_intake_service','service',202,True)
op('M05','post','/research-candidates/{candidate_id}/rejection','reject_research_candidate','ReasonRequest','ResearchCandidate','拒絕不適當候選並保留原始接收紀錄','analyst_reviewer',cas=True)
# M06
op('M06','get','/review-cases','list_review_cases',None,'ReviewCasePage','研究覆核工作佇列','analyst,analyst_reviewer')
op('M06','get','/review-cases/{review_case_id}','get_review_case',None,'ReviewCase','最新候選、修改請求及審查狀態','analyst,analyst_reviewer')
op('M06','post','/review-cases/{review_case_id}/assignment','assign_review_case','AssignmentRequest','ReviewCase','分派具資格覆核人員','research_supervisor',cas=True)
op('M06','post','/review-cases/{review_case_id}/conversations','open_servant_conversation','ConversationRequest','Conversation','啟動或續接 scoped Agora Servant 對話','analyst',status=201,cas=True)
op('M06','post','/review-cases/{review_case_id}/revision-requests','request_servant_revision','RevisionRequest','Revision','送出有版本前提的報告修改要求','analyst',status=202,cas=True)
op('M06','post','/webhooks/servant-revisions','receive_servant_revision','RevisionCallback','CallbackReceipt','接收新 artifact；過時回覆不覆寫審查版','servant_adapter','integration',202)
# M07
op('M07','post','/reports','create_report','ReportCreate','AdvisoryReport','將候選研究建立為正式投顧報告工作項目','analyst',status=201)
op('M07','post','/reports/{report_id}/versions','create_report_version','ReportVersionCreate','ReportVersion','建立不可覆寫的報告／訊息／受眾規則版本','analyst',status=201,cas=True)
op('M07','get','/report-versions/{report_version_id}','get_report_version',None,'ReportVersion','取得簽章包與研究證據鏈','analyst,analyst_reviewer,compliance_reviewer')
op('M07','post','/report-versions/{report_version_id}/reviews','record_report_review','ReportReviewRequest','ReviewDecision','專業或法遵審查綁定精確版次及雜湊','analyst_reviewer,compliance_reviewer',status=201,cas=True)
op('M07','post','/report-versions/{report_version_id}/freeze','freeze_report_version','FreezeReportRequest','ReportVersion','凍結報告、建議、各渠道文本與受眾規則','analyst',cas=True)
op('M07','post','/report-versions/{report_version_id}/signature','request_analyst_signature','AnalystSignatureRequest','SignatureSession','授權分析師簽署已完成所有審查的版本','analyst_signer',status=202,cas=True)
op('M07','post','/webhooks/analyst-signatures','receive_analyst_signature','AnalystSignatureCallback','CallbackReceipt','驗證最終簽章；成功事件自動觸發發布','signature_adapter','integration',202)
op('M07','post','/signatures/{signature_id}/revocation','revoke_signature','SignatureRevocationRequest','SignatureRevocation','新增撤銷證據，保留原簽章與下游撤回事件','compliance_supervisor',status=201,cas=True)
# M08
op('M08','post','/recommendation-series','create_recommendation_series','RecommendationSeriesCreate','RecommendationSeries','建立帶進、更新、帶出共同系列','analyst',status=201)
op('M08','post','/recommendation-series/{series_id}/versions','create_recommendation_version','RecommendationVersionCreate','RecommendationVersion','建議版次必須對應研究依據及後續簽章包','analyst',status=201,cas=True)
op('M08','get','/publications','list_publications',None,'PublicationPage','檢視簽章後自動發布狀態','analyst,service_agent,compliance_reviewer')
op('M08','get','/publications/{publication_id}','get_publication',None,'Publication','檢視版本、受眾快照、渠道狀態','analyst,service_agent,compliance_reviewer')
op('M08','post','/publications/{publication_id}/withdrawal','withdraw_publication','PublicationWithdrawal','Publication','撤回後停用尚未送出內容並保留歷史','analyst_reviewer,compliance_supervisor',cas=True)
op('M08','post','/publications/{publication_id}/correction-links','link_publication_correction','PublicationCorrection','Publication','關聯另已簽署的新更正版本，禁止覆寫原訊息','analyst_reviewer',cas=True)
# M09
op('M09','get','/member/feed','get_member_feed',None,'MemberFeedPage','LegendFlow 取得目前授權的已發布訊息','member','customer')
op('M09','get','/member/reports/{report_version_id}/access','request_member_report_access',None,'AccessTicket','即時檢查契約權限後提供短效報告存取票證','member','customer')
op('M09','post','/member/channel-bindings','bind_member_channel','ChannelBindingRequest','ChannelBinding','驗證官方渠道身分後設定接收渠道','member','customer',201)
op('M09','post','/member/communication-preferences','set_communication_preference','CommunicationPreferenceRequest','CommunicationPreference','區分服務通知與行銷同意','member','customer')
op('M09','get','/deliveries','list_deliveries',None,'DeliveryPage','逐客戶、逐渠道投遞狀態查詢','service_agent,delivery_operator')
op('M09','post','/webhooks/message-receipts','receive_message_receipt','DeliveryReceiptRequest','CallbackReceipt','驗證供應商 accepted/sent/delivered/failed，未知讀取仍未知','messaging_adapter','integration',202)
op('M09','post','/deliveries/{delivery_id}/reconciliation','reconcile_delivery','ReconciliationRequest','Delivery','查詢模糊投遞狀態後再判斷是否安全重送','delivery_operator',status=202,cas=True)
op('M09','post','/dead-letters/{dead_letter_id}/redrive','redrive_delivery_event','RedriveRequest','CallbackReceipt','經檢查內容期限與最新權限後重新入列','delivery_supervisor',status=202,cas=True)
# M10
op('M10','get','/crm/worklists','list_crm_worklists',None,'CrmWorkItemPage','續約、升級、補件、流失與留存工作清單','sales,sales_supervisor,marketing')
op('M10','post','/crm/opportunities','create_opportunity','OpportunityCreate','Opportunity','建立具體續約、升級或重新開發機會','sales',status=201)
op('M10','post','/crm/opportunities/{opportunity_id}/activities','record_sales_activity','SalesActivityCreate','SalesActivity','留下真實業務介入、客戶需求與下一步證據','sales',status=201,cas=True)
op('M10','post','/crm/opportunities/{opportunity_id}/next-actions','set_next_action','NextActionRequest','CrmWorkItem','指定負責人、期限與執行動作','sales,sales_supervisor',status=201,cas=True)
op('M10','post','/crm/opportunities/{opportunity_id}/conversion-claims','claim_conversion','ConversionClaim','Conversion','提出與成功續約／升級交易相連的業務歸因','sales',status=201,cas=True)
op('M10','post','/crm/conversions/{conversion_id}/decision','decide_conversion','ConversionDecision','Conversion','以證據及付款／生效事實裁定轉換歸因','sales_supervisor',cas=True)
op('M10','post','/crm/suppressions','create_crm_suppression','SuppressionRequest','Suppression','拒絕行銷、客訴或受限狀態暫停銷售觸達','service_agent,sales_supervisor',status=201)
op('M10','get','/crm/funnel-metrics','get_funnel_metrics',None,'FunnelMetrics','依來源、服務代碼與 cohort 檢視轉換／留存','sales_supervisor,marketing')
# M11
op('M11','get','/ownership/company-pool','list_company_pool',None,'PoolEntryPage','查看可重新開發的公司客戶池','sales,sales_supervisor')
op('M11','post','/customers/{customer_id}/maintenance-assignments','assign_maintenance_owner','MaintenanceAssignmentRequest','MaintenanceAssignment','指定維護責任與有效期間，不直接給獎金','sales_supervisor',status=201,cas=True)
op('M11','post','/ownership/pool-entries/{pool_entry_id}/claim','claim_pool_customer','PoolClaimRequest','MaintenanceAssignment','競爭領取以版本鎖保證只有一位新維護人','sales',status=201,cas=True)
op('M11','post','/ownership/assignments/{assignment_id}/release','release_maintenance_owner','ReasonRequest','PoolEntry','釋出維護關係但不撤回有效投顧服務','sales_supervisor',status=201,cas=True)
op('M11','post','/ownership/departures','record_employee_departure','EmployeeDepartureRequest','DepartureReceipt','業務離職即刻回公司池並停用授權','hr_operator',status=202)
op('M11','post','/ownership/pool-evaluations','evaluate_company_pool','PoolEvaluationRequest','PoolEvaluationResult','規則化計算付費服務中斷七日；不是七日未聯絡','ownership_service','service',202)
# M12
op('M12','post','/commissions/policies','create_commission_policy','CommissionPolicyCreate','CommissionPolicy','建立100/50/25、30/70及未稅收入規則版次','commission_operator',status=201)
op('M12','post','/commissions/policies/{policy_id}/approval','approve_commission_policy','PolicyApprovalRequest','CommissionPolicy','核准池率及尚待定義之交互規則，異人覆核','commission_approver',cas=True)
op('M12','post','/commissions/previews','preview_commissions','CommissionRunRequest','CommissionPreview','純試算：回傳假設、未核准條件及逐筆拆分','commission_operator',status=201)
op('M12','post','/commissions/runs','create_commission_run','CommissionRunRequest','CommissionRun','僅核准政策與已結算財務基礎可入正式台帳','commission_operator',status=202)
op('M12','get','/commissions/runs/{run_id}','get_commission_run',None,'CommissionRun','查看計算批次、差異與核准狀態','commission_operator,commission_approver')
op('M12','get','/commissions/entries','list_commission_entries',None,'CommissionEntryPage','依權限查看逐筆獎金歸因和退款調整','commission_operator,commission_approver,sales')
op('M12','post','/commissions/runs/{run_id}/approval','approve_commission_run','Decision','CommissionRun','異人核對批次與公式後核准','commission_approver',cas=True)
op('M12','post','/commissions/adjustments','create_commission_adjustment','CommissionAdjustmentRequest','CommissionAdjustment','原收入／原受領人連結的負向調整，不轉嫁接手人','commission_operator',status=201)
op('M12','post','/commissions/payout-batches','create_commission_payout_batch','PayoutBatchRequest','PayoutBatch','產生核准批次的薪酬介接檔與控制總額','payroll_operator',status=202)
# M13
op('M13','post','/service-cases','create_service_case','ServiceCaseCreate','ServiceCase','線上、電話、現場、郵件與官方訊息整併案件','service_agent','customer',201)
op('M13','get','/service-cases','list_service_cases',None,'ServiceCasePage','客服佇列及 SLA 優先順序','service_agent,service_supervisor')
op('M13','get','/service-cases/{case_id}','get_service_case',None,'ServiceCase','客服時間軸與關聯契約／研究／退款','service_agent')
op('M13','post','/service-cases/{case_id}/interactions','add_case_interaction','CaseInteractionCreate','CaseInteraction','記錄跨渠道互動與原始內容證據','service_agent','customer',201,True)
op('M13','post','/service-cases/{case_id}/assignment','assign_service_case','AssignmentRequest','ServiceCase','客服承辦人與業務維護人分離','service_supervisor',cas=True)
op('M13','post','/service-cases/{case_id}/escalations','escalate_service_case','CaseEscalationRequest','ServiceCase','升級分析師、財會或法遵並限制敏感資料','service_agent',cas=True)
op('M13','post','/service-cases/{case_id}/response-drafts','draft_service_response','ResponseDraftRequest','ResponseDraft','LLM 僅使用獲准知識與既有案件資訊起草','service_agent',status=202,cas=True)
op('M13','post','/service-cases/{case_id}/resolution','resolve_service_case','CaseResolutionRequest','ServiceCase','記錄解決方式及客戶告知證據','service_agent',cas=True)
op('M13','post','/service-cases/{case_id}/reopen','reopen_service_case','ReasonRequest','ServiceCase','客戶回覆或未解決問題重新開案','service_agent','customer',cas=True)
# M14
op('M14','post','/finance/invoices','create_invoice','InvoiceCreate','Invoice','依契約、稅務代碼與服務期建立應收','finance_operator',status=201)
op('M14','post','/finance/payment-intents','create_payment_intent','PaymentIntentCreate','PaymentIntent','建立支付意圖；簽約前後款項保持獨立狀態','finance_operator','customer',201)
op('M14','post','/webhooks/payments','receive_payment_webhook','PaymentCallback','CallbackReceipt','回呼驗簽與交易去重；不直接認列收入','payment_adapter','integration',202)
op('M14','post','/finance/settlement-imports','import_settlement','SettlementImportRequest','SettlementImport','匯入金流／銀行對帳檔與控制總數','finance_operator',status=202)
op('M14','get','/finance/reconciliation-items','list_reconciliation_items',None,'ReconciliationItemPage','列示短溢付、重複、手續費與未知交易','finance_operator')
op('M14','post','/finance/reconciliation-items/{item_id}/resolution','resolve_reconciliation_item','ReconciliationResolution','ReconciliationItem','覆核有差異對帳項目','finance_checker',cas=True)
op('M14','post','/finance/revenue-runs','run_revenue_recognition','RevenueRunRequest','RevenueRun','依核准服務期攤認收入並產生獎金基礎事件','finance_operator',status=202)
op('M14','post','/finance/refunds','request_refund','RefundCreate','Refund','區分未履約預收退回與已認列收入沖回','finance_operator,service_agent',status=201)
op('M14','post','/finance/refunds/{refund_id}/approval','approve_refund','Decision','Refund','退款金額／原付款／收入分攤由異人核准','finance_checker',cas=True)
op('M14','post','/finance/refunds/{refund_id}/execution','execute_refund','RefundExecutionRequest','Refund','受控執行核准退款，timeout 先查帳再重送','refund_service','service',202,True)
op('M14','post','/webhooks/refunds','receive_refund_webhook','RefundCallback','CallbackReceipt','退款供應商確認及帳務沖回','payment_adapter','integration',202)
op('M14','post','/finance/accounting-exports','create_accounting_export','AccountingExportRequest','AccountingExport','輸出平衡借貸、來源索引與會計映射版本','finance_operator',status=202)
op('M14','post','/finance/accounting-exports/{export_id}/acknowledgement','acknowledge_accounting_export','ExportAckRequest','AccountingExport','會計系統回覆成功／部分失敗，禁止重複過帳','accounting_adapter','integration',202)
op('M14','get','/audit/events','list_audit_events',None,'AuditEventPage','依權限查核系統與作業稽核紀錄','internal_auditor,compliance_reviewer')
op('M14','post','/audit/evidence-exports','create_evidence_export','EvidenceExportRequest','EvidenceExport','重建客戶或發布完整證據鏈並留匯出存取紀錄','internal_auditor',status=202)
op('M14','post','/audit/legal-holds','create_legal_hold','LegalHoldRequest','LegalHold','受控保全阻止自動刪除','compliance_supervisor',status=201)
op('M14','post','/audit/control-exceptions','record_control_exception','ControlExceptionRequest','ControlException','可追蹤內控缺失、改善人、期限與覆核','internal_auditor',status=201)
S={}
def ref(n): return {'$ref':'#/components/schemas/'+n}
def string(desc='',maxlen=500): return dict(type='string',maxLength=maxlen,**({'description':desc} if desc else {}))
def enum(*vals): return {'type':'string','enum':list(vals)}
U={'type':'string','format':'uuid'}
T={'type':'string','format':'date-time','pattern':r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$','description':'UTC RFC3339；不得用來源交易時間倒填發布時間。'}
D={'type':'string','format':'date'}
SHA={'type':'string','pattern':'^[a-f0-9]{64}$'}
DEC={'type':'string','pattern':r'^-?(?:0|[1-9]\d{0,13})(?:\.\d{1,6})?$','description':'十進位字串，最多6位小數；禁止binary float算帳。'}
POS={'type':'integer','minimum':1}
B={'type':'boolean'}
def arr(item,mi=0,ma=500): return dict(type='array',items=item,minItems=mi,maxItems=ma)
def nullable(schema): return {'anyOf':[schema,{'type':'null'}]}
def obj(fields,required=None,desc=''):
 return dict(type='object',additionalProperties=False,properties=fields,required=required if required is not None else list(fields),**({'description':desc} if desc else {}))
def add(n,f,r=None,d=''): S[n]=obj(f,r,d)
def resource(n,f,r=None):
 base={'id':U,'tenant_id':U,'version':POS,'created_at':T,'updated_at':T,'allowed_actions':arr(string('後端依角色、狀態、資格與SoD計算的operation_id',100),0,40)}
 add(n,{**base,**f},list(base)+(list(f) if r is None else r))
def page(n,item):
 add(n,{'items':arr(ref(item),0,200),'next_cursor':nullable(string(maxlen=1024)),'as_of':T,'total_estimate':nullable({'type':'integer','minimum':0})},['items','next_cursor','as_of'])
add('Money',{'amount':DEC,'currency':{'type':'string','pattern':'^[A-Z]{3}$'}},d='幣別必須與契約/來源收入一致；四捨五入在計獎政策定義的結算層做。')
add('ExternalRef',{'system':enum('pantheon','agora','legendflow','signature_provider','screening_provider','payment_provider','accounting_provider','messaging_provider'),'external_id':string(maxlen=256),'kind':string(maxlen=80),'version':string(maxlen=128),'checksum_sha256':SHA},['system','external_id','kind'])
add('ReasonRequest',{'reason_code':string(maxlen=80),'note':string(maxlen=4000),'evidence_asset_ids':arr(U,0,30)},['reason_code'])
add('Decision',{'decision':enum('approve','reject','request_changes'),'reason_code':string(maxlen=80),'note':string(maxlen=4000),'evidence_asset_ids':arr(U,0,30)},['decision','reason_code'])
add('AssignmentRequest',{'assignee_id':U,'reason_code':string(maxlen=80),'effective_at':T},['assignee_id','reason_code'])
add('CallbackReceipt',{'receipt_id':U,'event_id':U,'status':enum('accepted','duplicate','quarantined'),'received_at':T,'correlation_id':U})
add('DocumentRef',{'asset_id':U,'sha256':SHA,'media_type':enum('application/pdf','application/json','text/plain','image/jpeg','image/png'),'classification':enum('internal','customer_confidential','aml_restricted','research_confidential'),'scan_status':enum('pending','clean','rejected')})
# M01
add('CustomerCreate',{'external_system':enum('legendflow','manual'),'verified_identity_proof_ref':U,'display_name':string(maxlen=100),'marketing_source_ref':nullable(string(maxlen=160))},['external_system','verified_identity_proof_ref'])
resource('Customer',{'status':enum('prospect','applicant','active','inactive','blocked','merged'),'display_name':string(maxlen=100),'masked_email':nullable(string(maxlen=254)),'masked_phone':nullable(string(maxlen=40)),'maintenance_owner_id':nullable(U),'original_marketing_attribution_id':nullable(U),'merged_into_customer_id':nullable(U)},['status','display_name','masked_email','masked_phone'])
add('IdentityLinkRequest',{'provider':enum('legendflow','line','email','phone'),'verified_proof_ref':U,'proof_expires_at':T},['provider','verified_proof_ref'])
resource('ExternalIdentity',{'customer_id':U,'provider':enum('legendflow','line','email','phone'),'subject_hash':SHA,'verified_at':T,'status':enum('verified','revoked')})
add('CustomerMergeRequest',{'source_customer_id':U,'target_customer_id':U,'evidence_asset_ids':arr(U,1,20),'reason_code':string(maxlen=80)})
resource('MergeCase',{'source_customer_id':U,'target_customer_id':U,'status':enum('proposed','approved','rejected','applied'),'proposer_id':U,'reviewer_id':nullable(U)})
add('ApplicationCreate',{'customer_id':U,'package_version_id':U,'service_selections':arr(ref('ServiceSelection'),1,10),'acquisition_touch_ref':nullable(string(maxlen=160)),'intent':enum('new','renewal','upgrade','reactivation'),'preceding_contract_id':nullable(U)},['customer_id','package_version_id','service_selections','intent'])
add('ServiceSelection',{'service_code':string(maxlen=80),'offer_version':string(maxlen=40),'quantity':{'type':'integer','minimum':1,'maximum':100},'billing_interval':enum('month','year','one_time'),'quoted_price':ref('Money'),'tax_mapping_version':string(maxlen=40)})
resource('Application',{'customer_id':U,'package_version_id':U,'state':enum('draft','customer_signed','due_diligence_pending','review_in_progress','accepted','rejected','withdrawn'),'customer_signed_at':nullable(T),'company_accepted_at':nullable(T),'kyc_status':enum('not_started','pending','approved','rejected','refresh_required'),'suitability_status':enum('pending','suitable','conditional','not_suitable'),'business_clearance':enum('pending','approved','denied','expired'),'payment_status':enum('not_required','pending','authorized','paid','refunded','disputed'),'contract_id':nullable(U)},['customer_id','package_version_id','state','customer_signed_at','company_accepted_at','kyc_status','suitability_status','business_clearance','payment_status'])
add('ConsentRequest',{'purpose':enum('privacy','electronic_signing','marketing','service_notification','aml_declaration','suitability_declaration'),'document_version':string(maxlen=80),'document_sha256':SHA,'granted':B,'evidence_asset_id':U})
resource('Consent',{'customer_id':U,'purpose':string(maxlen=80),'document_version':string(maxlen=80),'document_sha256':SHA,'granted':B,'recorded_at':T,'evidence_asset_id':U})
# M02
add('PackageCreate',{'package_code':string(maxlen=80),'version_label':string(maxlen=40),'documents':arr(ref('DocumentRef'),1,20),'service_codes':arr(string(maxlen=80),1,20),'effective_from':T,'acceptance_terms_version':string('客戶簽署為申請，公司承諾另行記錄；條款需核定',80)})
resource('ContractPackage',{'package_code':string(maxlen=80),'version_label':string(maxlen=40),'documents':arr(ref('DocumentRef'),1,20),'state':enum('draft','approved','retired'),'approved_by':nullable(U),'effective_from':T,'acceptance_terms_version':string(maxlen=80)})
add('CustomerSignatureRequest',{'package_version_id':U,'package_sha256':SHA,'electronic_signing_consent_id':U,'return_route':enum('application_progress','member_contracts')})
resource('SignatureSession',{'purpose':enum('customer_application','analyst_report'),'subject_id':U,'provider_session_ref':string(maxlen=160),'redirect_url':{'type':'string','format':'uri','pattern':'^https://'},'expires_at':T,'status':enum('pending','verified','failed','expired')})
add('CustomerSignatureCallback',{'event_id':U,'provider_session_ref':string(maxlen=160),'application_id':U,'package_version_id':U,'document_sha256':SHA,'signed_asset_id':U,'signed_file_sha256':SHA,'provider_signed_at':T,'verification_evidence_asset_id':U,'result':enum('signed','failed','expired')})
add('CompanyAcceptanceRequest',{'decision':enum('accept','reject','request_changes'),'kyc_submission_id':U,'suitability_assessment_id':U,'business_clearance_id':U,'package_version_id':U,'customer_signature_id':U,'intended_effective_at':T,'reason_code':string(maxlen=80),'company_signature_evidence_asset_id':nullable(U),'payment_condition_version':string(maxlen=80)},['decision','kyc_submission_id','suitability_assessment_id','business_clearance_id','package_version_id','customer_signature_id','reason_code','payment_condition_version'])
resource('CompanyAcceptance',{'application_id':U,'decision':enum('accept','reject','request_changes'),'actor_id':U,'decided_at':T,'decision_evidence_ids':arr(U,1,30),'intended_effective_at':nullable(T),'contract_id':nullable(U)})
resource('Contract',{'customer_id':U,'application_id':U,'package_version_id':U,'state':enum('pending_company_acceptance','accepted','effective','suspended','expired','terminated'),'customer_signed_at':T,'company_accepted_at':nullable(T),'effective_at':nullable(T),'service_starts_at':nullable(T),'service_ends_at':nullable(T),'terminated_at':nullable(T),'service_selections':arr(ref('ServiceSelection'),1,10),'preceding_contract_id':nullable(U)})
add('AmendmentRequest',{'amendment_type':enum('upgrade','renewal','scope_change'),'new_package_version_id':U,'service_selections':arr(ref('ServiceSelection'),1,10),'effective_at':T,'opportunity_id':nullable(U)},['amendment_type','new_package_version_id','service_selections','effective_at'])
add('TerminationRequest',{'requested_at':T,'effective_at':T,'reason_code':string(maxlen=80),'customer_request_evidence_id':U,'refund_evaluation_required':B})
resource('Entitlement',{'customer_id':U,'contract_id':U,'service_code':string(maxlen=80),'state':enum('locked','eligible','active','suspended','expired','revoked'),'valid_from':T,'valid_until':T,'last_decision_id':U,'restriction_codes':arr(string(maxlen=80),0,20)})
add('EntitlementEvaluationRequest',{'cause_event_id':U,'policy_version':string(maxlen=80),'evaluation_at':T},['cause_event_id','policy_version'])
resource('EntitlementDecision',{'contract_id':U,'decision':enum('grant','keep','suspend','expire','revoke','deny'),'policy_version':string(maxlen=80),'condition_snapshots':arr(ref('GateSnapshot'),1,20),'entitlement_ids':arr(U,0,20),'decided_at':T})
add('GateSnapshot',{'gate':enum('customer_signature','company_acceptance','contract_effective','kyc','suitability','business_clearance','payment','service_period','credential','review','signature'),'result':enum('pass','pending','fail','expired'),'record_id':U,'record_version':POS,'evaluated_at':T})
add('SuspensionRequest',{'reason_code':string(maxlen=80),'evidence_ref':U,'until':nullable(T),'customer_notice_template_version':string(maxlen=80)},['reason_code','evidence_ref'])
# M03
add('KycDraftRequest',{'form_version':string(maxlen=80),'customer_signature_id':U})
add('QuestionAnswer',{'question_id':string(maxlen=80),'answer_type':enum('text','choice','multi_choice','number','boolean','date'),'text_value':nullable(string(maxlen=2000)),'choice_codes':arr(string(maxlen=80),0,30),'number_value':nullable(DEC),'boolean_value':nullable(B),'date_value':nullable(D)},['question_id','answer_type'])
resource('KycSubmission',{'application_id':U,'customer_id':U,'form_version':string(maxlen=80),'state':enum('not_started','in_progress','submitted','incomplete','under_review','approved','rejected','refresh_required'),'answer_snapshot_asset_id':nullable(U),'answer_sha256':nullable(SHA),'document_ids':arr(U,0,30),'submitted_at':nullable(T),'reviewer_id':nullable(U),'reviewed_at':nullable(T),'next_review_due_at':nullable(T),'missing_field_codes':arr(string(maxlen=80),0,100)})
add('DocumentUploadRequest',{'document_type':enum('identity_front','identity_back','passport','address_evidence','corporate_registry','beneficial_owner_evidence','other_approved'),'filename_extension':enum('pdf','jpg','jpeg','png'),'media_type':enum('application/pdf','image/jpeg','image/png'),'size_bytes':{'type':'integer','minimum':1,'maximum':20971520},'sha256':SHA})
add('UploadTicket',{'asset_id':U,'upload_url':{'type':'string','format':'uri','pattern':'^https://'},'expires_at':T,'max_size_bytes':{'type':'integer','maximum':20971520},'scan_required':{'type':'boolean','const':True},'classification':enum('customer_confidential','aml_restricted')})
add('KycSubmitRequest',{'form_version':string(maxlen=80),'answers':arr(ref('QuestionAnswer'),1,150),'document_ids':arr(U,1,30),'declaration_consent_id':U})
add('KycDecisionRequest',{'decision':enum('approve','reject','request_information'),'identity_verification_method':enum('approved_remote_identity','verified_e_signature','manual_document_review','approved_equivalent'),'verification_evidence_ids':arr(U,1,30),'missing_field_codes':arr(string(maxlen=80),0,100),'reason_code':string(maxlen=80),'next_review_due_at':nullable(T)},['decision','identity_verification_method','verification_evidence_ids','reason_code'])
add('SuitabilityRequest',{'kyc_submission_id':U,'questionnaire_version':string(maxlen=80),'rule_version':string(maxlen=80),'answers':arr(ref('QuestionAnswer'),1,100),'service_codes':arr(string(maxlen=80),1,20),'decision':enum('suitable','conditional','not_suitable'),'conditions':arr(string(maxlen=300),0,20),'reason_code':string(maxlen=80),'next_review_due_at':T})
resource('SuitabilityAssessment',{'application_id':U,'customer_id':U,'kyc_submission_id':U,'questionnaire_version':string(maxlen=80),'rule_version':string(maxlen=80),'state':enum('pending','suitable','conditional','not_suitable','reassessment_due'),'conditions':arr(string(maxlen=300),0,20),'assessed_by':U,'assessed_at':T,'next_review_due_at':T})
add('RefreshRequest',{'trigger':enum('scheduled','identity_change','risk_change','service_change','regulatory_request'),'reason_code':string(maxlen=80),'due_at':T,'form_version':string(maxlen=80)})
# M04
add('AmlDeclarationRequest',{'form_version':string(maxlen=80),'declaration_consent_id':U,'restricted_answer_asset_id':U,'restricted_document_ids':arr(U,0,30),'customer_signature_id':U})
resource('AmlDeclarationReceipt',{'application_id':U,'received_at':T,'state':enum('received','requires_information'),'is_business_clearance':{'type':'boolean','const':False}})
add('ScreeningRequest',{'application_id':U,'customer_id':U,'kyc_submission_id':U,'screening_policy_version':string(maxlen=80),'scope':arr(enum('sanctions','terrorist_financing','pep','adverse_media'),1,4),'restricted_identity_asset_id':U,'reason_code':string(maxlen=80)})
resource('AmlScreening',{'application_id':U,'customer_id':U,'provider_ref':string(maxlen=160),'policy_version':string(maxlen=80),'list_version':nullable(string(maxlen=160)),'state':enum('pending','clear','possible_match','confirmed_match','error'),'result_asset_id':nullable(U),'screened_at':nullable(T),'expires_at':nullable(T),'case_id':nullable(U)})
add('ScreeningCallback',{'event_id':U,'screening_id':U,'provider_ref':string(maxlen=160),'list_version':string(maxlen=160),'screened_at':T,'result':enum('clear','possible_match','confirmed_match','error'),'restricted_result_asset_id':U,'result_sha256':SHA})
resource('AmlCase',{'customer_id':U,'application_id':U,'screening_ids':arr(U,1,50),'state':enum('open','under_review','escalated','cleared','rejected','closed'),'risk_level':enum('low','standard','high'),'assignee_id':nullable(U),'restricted_evidence_ids':arr(U,0,100),'opened_at':T,'next_review_due_at':nullable(T)})
add('AmlActionRequest',{'action':enum('request_edd','request_information','escalate','record_investigation','record_filing_reference'),'reason_code':string(maxlen=80),'restricted_note_asset_id':U,'restricted_evidence_ids':arr(U,0,30),'due_at':nullable(T)},['action','reason_code','restricted_note_asset_id'])
add('AmlDecisionRequest',{'decision':enum('approve','deny','request_information'),'risk_level':enum('low','standard','high'),'reason_code':string(maxlen=80),'restricted_evidence_ids':arr(U,1,50),'independent_reviewer_id':nullable(U),'expires_at':nullable(T)},['decision','risk_level','reason_code','restricted_evidence_ids'])
resource('BusinessClearance',{'application_id':U,'customer_id':U,'decision':enum('pending','approved','denied','expired'),'decided_at':nullable(T),'expires_at':nullable(T),'policy_version':string(maxlen=80)})
# M05-06
add('CandidateCreate',{'external_event_id':U,'origin':enum('persona_autonomous','analyst_initiated'),'persona_ref':nullable(string(maxlen=160)),'pool_ref':nullable(string(maxlen=160)),'ticket_ref':ref('ExternalRef'),'plan_ref':nullable(ref('ExternalRef')),'artifact_ref':ref('ExternalRef'),'artifact_content_sha256':SHA,'evidence_bundle_refs':arr(ref('ExternalRef'),1,100),'source_created_at':T,'source_available_at':T,'source_committed_at':nullable(T),'data_scope':{'type':'string','const':'research_only'},'provenance_snapshot_asset_id':U,'license_scope_ref':string(maxlen=160)},['external_event_id','origin','ticket_ref','artifact_ref','artifact_content_sha256','evidence_bundle_refs','source_created_at','source_available_at','data_scope','provenance_snapshot_asset_id','license_scope_ref'])
resource('ResearchCandidate',{'external_event_id':U,'origin':enum('persona_autonomous','analyst_initiated'),'artifact_ref':ref('ExternalRef'),'artifact_content_sha256':SHA,'evidence_bundle_refs':arr(ref('ExternalRef'),1,100),'state':enum('received','validating','accepted','quarantined','rejected'),'source_created_at':T,'source_available_at':T,'received_at':T,'review_case_id':nullable(U),'validation_failure_codes':arr(string(maxlen=80),0,30)})
add('CandidateValidationRequest',{'observed_content_sha256':SHA,'evidence_access_confirmed':B,'license_scope_confirmed':B,'source_snapshot_asset_id':U})
resource('ReviewCase',{'candidate_id':U,'current_artifact_ref':ref('ExternalRef'),'current_report_version_id':nullable(U),'state':enum('queued','assigned','under_review','revision_requested','ready_to_sign','signed','rejected','stale'),'assignee_id':nullable(U),'professional_reviewer_id':nullable(U),'compliance_review_required':B,'pending_revision_ids':arr(U,0,30),'source_version_stale':B})
add('ConversationRequest',{'candidate_id':U,'artifact_ref':ref('ExternalRef'),'existing_conversation_ref':nullable(ref('ExternalRef')),'allowed_evidence_refs':arr(ref('ExternalRef'),1,100),'purpose':enum('review','revision','counter_analysis','evidence_request')})
resource('Conversation',{'review_case_id':U,'external_conversation_ref':ref('ExternalRef'),'scoped_artifact_ref':ref('ExternalRef'),'status':enum('opening','active','closed','failed'),'audit_transcript_asset_id':nullable(U)})
add('RevisionRequest',{'conversation_id':U,'base_artifact_ref':ref('ExternalRef'),'base_content_sha256':SHA,'instructions':string(maxlen=12000),'required_evidence_refs':arr(ref('ExternalRef'),0,100),'requested_output':enum('revised_report','additional_evidence','counter_analysis','message_draft')})
resource('Revision',{'review_case_id':U,'conversation_id':U,'base_artifact_ref':ref('ExternalRef'),'state':enum('queued','running','completed','failed','stale','cancelled'),'result_artifact_ref':nullable(ref('ExternalRef')),'result_content_sha256':nullable(SHA),'requested_by':U,'completed_at':nullable(T)})
add('RevisionCallback',{'event_id':U,'revision_id':U,'conversation_ref':ref('ExternalRef'),'base_artifact_ref':ref('ExternalRef'),'result_artifact_ref':nullable(ref('ExternalRef')),'result_content_sha256':nullable(SHA),'status':enum('completed','failed'),'completed_at':T,'transcript_snapshot_asset_id':U},['event_id','revision_id','conversation_ref','base_artifact_ref','status','completed_at','transcript_snapshot_asset_id'])
# M07-08
add('ReportCreate',{'review_case_id':U,'title':string(maxlen=200),'instrument_refs':arr(ref('InstrumentRef'),1,50),'report_type':enum('initial','update','exit','supplement','correction'),'preceding_report_id':nullable(U)},['review_case_id','title','instrument_refs','report_type'])
add('InstrumentRef',{'market':string(maxlen=40),'instrument_code':string(maxlen=40),'instrument_type':enum('equity','etf','fund','bond','other_approved')})
resource('AdvisoryReport',{'review_case_id':U,'title':string(maxlen=200),'instrument_refs':arr(ref('InstrumentRef'),1,50),'report_type':enum('initial','update','exit','supplement','correction'),'current_version_id':nullable(U),'state':enum('draft','active','withdrawn','archived')})
add('ChannelText',{'channel':enum('legendflow','line','sms','email'),'locale':enum('zh-TW'),'body':string(maxlen=12000),'body_sha256':SHA,'template_version':string(maxlen=80),'recommendation_version_ids':arr(U,1,50)},d='完整具實質投資建議的文字需先納入審查簽章包；簽後只可替換已核定的無實質變動傳輸欄位。')
add('AudiencePolicy',{'policy_id':U,'policy_version':string(maxlen=80),'service_codes':arr(string(maxlen=80),1,30),'required_entitlement_state':{'type':'string','const':'active'},'restriction_policy_version':string(maxlen=80),'audience_policy_sha256':SHA})
add('ReportVersionCreate',{'source_candidate_id':U,'source_artifact_ref':ref('ExternalRef'),'report_asset_id':U,'report_content_sha256':SHA,'evidence_snapshot_asset_ids':arr(U,1,100),'recommendation_version_ids':arr(U,1,50),'channel_texts':arr(ref('ChannelText'),1,8),'audience_policy':ref('AudiencePolicy'),'valid_from':T,'valid_until':T,'supersedes_version_id':nullable(U)},['source_candidate_id','source_artifact_ref','report_asset_id','report_content_sha256','evidence_snapshot_asset_ids','recommendation_version_ids','channel_texts','audience_policy','valid_from','valid_until'])
resource('ReportVersion',{'report_id':U,'version_number':POS,'source_candidate_id':U,'source_artifact_ref':ref('ExternalRef'),'report_asset_id':U,'report_content_sha256':SHA,'bundle_manifest_asset_id':nullable(U),'bundle_manifest_sha256':nullable(SHA),'signed_file_sha256':nullable(SHA),'state':enum('draft','in_review','changes_requested','ready_to_sign','signature_pending','signed','superseded','withdrawn'),'channel_texts':arr(ref('ChannelText'),1,8),'audience_policy':ref('AudiencePolicy'),'valid_from':T,'valid_until':T,'frozen_at':nullable(T),'signed_at':nullable(T),'signature_id':nullable(U),'required_review_ids':arr(U,0,10)})
add('ReportReviewRequest',{'review_type':enum('professional','compliance'),'decision':enum('approve','request_changes','reject'),'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'review_policy_version':string(maxlen=80),'checklist_result_asset_id':U,'reason_code':string(maxlen=80)})
resource('ReviewDecision',{'report_version_id':U,'review_type':enum('professional','compliance'),'decision':enum('approve','request_changes','reject'),'reviewer_id':U,'reviewed_at':T,'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'checklist_result_asset_id':U})
add('FreezeReportRequest',{'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'required_review_ids':arr(U,1,10),'canonicalization':{'type':'string','const':'RFC8785'}})
add('AnalystSignatureRequest',{'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'credential_id':U,'required_review_ids':arr(U,1,10),'signing_intent_confirmed':{'type':'boolean','const':True}})
add('AnalystSignatureCallback',{'event_id':U,'signature_request_id':U,'provider_session_ref':string(maxlen=160),'report_version_id':U,'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'signed_asset_id':U,'signed_file_sha256':SHA,'signer_subject_ref':string(maxlen=256),'signed_at':T,'verification_evidence_asset_id':U,'verification_result':enum('verified','failed'),'certificate_chain_asset_id':nullable(U),'trusted_timestamp_asset_id':nullable(U)},['event_id','signature_request_id','provider_session_ref','report_version_id','report_content_sha256','bundle_manifest_sha256','signed_asset_id','signed_file_sha256','signer_subject_ref','signed_at','verification_evidence_asset_id','verification_result'])
add('SignatureRevocationRequest',{'reason_code':string(maxlen=80),'evidence_asset_id':U,'effective_at':T,'notify_recipients':B})
resource('SignatureRevocation',{'signature_id':U,'reason_code':string(maxlen=80),'evidence_asset_id':U,'effective_at':T,'revoked_by':U,'publication_withdrawal_event_id':U})
add('RecommendationSeriesCreate',{'instrument_ref':ref('InstrumentRef'),'strategy_scope_ref':string(maxlen=160),'research_origin_ref':ref('ExternalRef')})
resource('RecommendationSeries',{'instrument_ref':ref('InstrumentRef'),'strategy_scope_ref':string(maxlen=160),'state':enum('open','closed','withdrawn'),'latest_version_id':nullable(U)})
add('RecommendationVersionCreate',{'action':enum('entry','update','exit','hold','correction'),'rationale':string(maxlen=12000),'supporting_report_version_ids':arr(U,1,20),'valid_from':T,'valid_until':T,'supersedes_version_id':nullable(U)},['action','rationale','supporting_report_version_ids','valid_from','valid_until'])
resource('RecommendationVersion',{'series_id':U,'version_number':POS,'action':enum('entry','update','exit','hold','correction'),'rationale_asset_id':U,'supporting_report_version_ids':arr(U,1,20),'state':enum('draft','under_review','signed','expired','withdrawn'),'valid_from':T,'valid_until':T,'supersedes_version_id':nullable(U)})
resource('Publication',{'report_version_id':U,'signature_id':U,'recommendation_version_ids':arr(U,1,50),'bundle_manifest_sha256':SHA,'state':enum('queued','publishing','published','partially_failed','expired','corrected','withdrawn'),'created_from_signature_event_id':U,'audience_snapshot_id':U,'recipient_count':{'type':'integer','minimum':0},'published_at':nullable(T),'valid_until':T,'correction_publication_id':nullable(U),'withdrawal_reason_code':nullable(string(maxlen=80))})
add('PublicationWithdrawal',{'reason_code':string(maxlen=80),'evidence_asset_id':U,'notify_recipients':B,'notice_template_version':string(maxlen=80)})
add('PublicationCorrection',{'replacement_publication_id':U,'replacement_report_version_id':U,'reason_code':string(maxlen=80)})
# M09
add('MemberFeedItem',{'publication_id':U,'recommendation_series_id':U,'recommendation_version_id':U,'action':enum('entry','update','exit','hold','correction'),'approved_text':string(maxlen=12000),'body_sha256':SHA,'report_version_ids':arr(U,1,20),'published_at':T,'valid_until':T,'state':enum('published','corrected','withdrawn'),'correction_publication_id':nullable(U)})
add('AccessTicket',{'report_version_id':U,'download_url':{'type':'string','format':'uri','pattern':'^https://'},'expires_at':T,'content_sha256':SHA,'audit_access_id':U},d='票證最長120秒，兌換時再次確認subject/權限；URL不可放日誌或事件。')
add('ChannelBindingRequest',{'channel':enum('line','sms','email'),'verified_channel_proof_ref':U,'service_delivery_enabled':B})
resource('ChannelBinding',{'customer_id':U,'channel':enum('line','sms','email'),'masked_destination':string(maxlen=200),'state':enum('verified','unsubscribed','blocked','revoked'),'service_delivery_enabled':B})
add('CommunicationPreferenceRequest',{'channel':enum('legendflow','line','sms','email'),'purpose':enum('service','marketing'),'enabled':B,'consent_id':U})
resource('CommunicationPreference',{'customer_id':U,'channel':enum('legendflow','line','sms','email'),'purpose':enum('service','marketing'),'enabled':B,'consent_id':U})
resource('Delivery',{'publication_id':U,'customer_id':U,'channel':enum('legendflow','line','sms','email'),'content_sha256':SHA,'state':enum('queued','accepted','sent','delivered','failed','expired','suppressed','unknown'),'provider_message_ref':nullable(string(maxlen=200)),'attempt_count':{'type':'integer','minimum':0},'accepted_at':nullable(T),'sent_at':nullable(T),'delivered_at':nullable(T),'read_at':nullable(T),'read_capability':enum('unsupported','supported','unknown'),'last_entitlement_decision_id':nullable(U),'last_error_code':nullable(string(maxlen=100))})
add('DeliveryReceiptRequest',{'event_id':U,'provider_message_ref':string(maxlen=200),'delivery_id':U,'provider_sequence':nullable({'type':'integer','minimum':0}),'provider_status':enum('accepted','sent','delivered','failed','read'),'provider_occurred_at':T,'provider_receipt_asset_id':U},['event_id','provider_message_ref','delivery_id','provider_status','provider_occurred_at','provider_receipt_asset_id'])
add('ReconciliationRequest',{'reason_code':string(maxlen=80),'last_known_provider_message_ref':nullable(string(maxlen=200))},['reason_code'])
add('RedriveRequest',{'reason_code':string(maxlen=80),'expected_event_id':U,'approved_by':U,'revalidate_entitlement':{'type':'boolean','const':True},'revalidate_content_expiry':{'type':'boolean','const':True}})
# M10
add('OpportunityCreate',{'customer_id':U,'type':enum('new_subscription','renewal','upgrade','retention','reactivation'),'service_code':string(maxlen=80),'contract_id':nullable(U),'reason_code':string(maxlen=80),'owner_id':U,'due_at':T},['customer_id','type','service_code','reason_code','owner_id','due_at'])
resource('Opportunity',{'customer_id':U,'type':enum('new_subscription','renewal','upgrade','retention','reactivation'),'service_code':string(maxlen=80),'owner_id':U,'state':enum('open','qualified','proposal','awaiting_customer','converted','lost','suppressed'),'contract_id':nullable(U),'due_at':T,'converted_at':nullable(T)})
add('SalesActivityCreate',{'activity_type':enum('two_way_message','completed_call','meeting','needs_resolution','proposal','follow_up'),'occurred_at':T,'customer_response_evidence_id':nullable(U),'activity_evidence_asset_id':U,'needs_summary':string(maxlen=2000),'next_action':string(maxlen=500),'next_action_due_at':T,'outcome':enum('contacted','needs_identified','proposal_requested','follow_up','declined','converted')},['activity_type','occurred_at','activity_evidence_asset_id','needs_summary','next_action','next_action_due_at','outcome'])
resource('SalesActivity',{'opportunity_id':U,'customer_id':U,'actor_id':U,'activity_type':enum('two_way_message','completed_call','meeting','needs_resolution','proposal','follow_up'),'occurred_at':T,'evidence_asset_id':U,'qualifies_for_attribution':B,'attribution_review_reason':nullable(string(maxlen=80))})
add('NextActionRequest',{'owner_id':U,'action_type':enum('call','message','meeting','complete_documents','renewal_offer','upgrade_assessment','resolve_issue'),'due_at':T,'reason_code':string(maxlen=80),'note':string(maxlen=2000)},['owner_id','action_type','due_at','reason_code'])
resource('CrmWorkItem',{'customer_id':U,'opportunity_id':nullable(U),'worklist':enum('incomplete_application','pending_review','unpaid','renewal','upgrade','retention','reactivation','company_pool'),'owner_id':nullable(U),'action_type':string(maxlen=80),'due_at':T,'state':enum('open','in_progress','completed','suppressed','cancelled'),'reason_codes':arr(string(maxlen=80),1,20),'evidence_refs':arr(U,0,20),'suggestion_origin':enum('rule','llm','human'),'suppression_applied':B})
add('ConversionClaim',{'application_id':U,'contract_id':U,'finance_transaction_ref':U,'sales_activity_ids':arr(U,1,50),'conversion_type':enum('new_subscription','renewal','upgrade','reactivation'),'claimed_salesperson_id':U,'reason_code':string(maxlen=80)})
resource('Conversion',{'customer_id':U,'opportunity_id':U,'contract_id':U,'conversion_type':enum('new_subscription','renewal','upgrade','reactivation'),'claimed_salesperson_id':U,'approved_salesperson_id':nullable(U),'state':enum('claimed','under_review','approved','rejected','disputed','reversed'),'evidence_activity_ids':arr(U,1,50),'decision_at':nullable(T),'finance_transaction_ref':U})
add('ConversionDecision',{'decision':enum('approve','reject','dispute'),'reason_code':string(maxlen=80),'approved_salesperson_id':nullable(U),'qualifying_activity_ids':arr(U,0,50),'causality_evidence_asset_id':U},['decision','reason_code','causality_evidence_asset_id'])
add('SuppressionRequest',{'customer_id':U,'scope':enum('marketing_contact','sales_contact','all_nonessential_contact'),'reason_category':enum('opt_out','open_complaint','refund_pending','customer_request','restricted_process'),'reason_ref':U,'expires_at':nullable(T)})
resource('Suppression',{'customer_id':U,'scope':enum('marketing_contact','sales_contact','all_nonessential_contact'),'reason_category':enum('opt_out','open_complaint','refund_pending','customer_request','restricted_process'),'active':B,'expires_at':nullable(T)})
add('FunnelMetric',{'stage':enum('lead','application_started','customer_signed','due_diligence','accepted','paid','active','renewal','upgrade','churn'),'count':{'type':'integer','minimum':0},'cohort_key':string(maxlen=160),'service_code':string(maxlen=80),'period_start':D,'period_end':D})
add('FunnelMetrics',{'items':arr(ref('FunnelMetric'),0,500),'as_of':T,'attribution_policy_version':string(maxlen=80),'data_complete_through':T})
# M11
add('MaintenanceAssignmentRequest',{'salesperson_id':U,'effective_from':T,'reason_code':string(maxlen=80),'previous_assignment_id':nullable(U),'handoff_evidence_asset_id':nullable(U)},['salesperson_id','effective_from','reason_code'])
resource('MaintenanceAssignment',{'customer_id':U,'salesperson_id':U,'valid_from':T,'valid_until':nullable(T),'reason_code':string(maxlen=80),'state':enum('active','released','departure','superseded'),'source_pool_entry_id':nullable(U),'commission_entitlement_granted':{'type':'boolean','const':False}})
resource('PoolEntry',{'customer_id':U,'entered_at':T,'entry_reason':enum('unassigned_web','employee_departure','paid_service_lapse','manual_release'),'paid_service_lapse_started_at':nullable(T),'state':enum('available','claimed','suppressed'),'claimed_by':nullable(U),'policy_version':string(maxlen=80)})
add('PoolClaimRequest',{'salesperson_id':U,'first_contact_due_at':T,'reason_code':string(maxlen=80)})
add('EmployeeDepartureRequest',{'employee_id':U,'effective_at':T,'hr_evidence_asset_id':U,'reason_code':string(maxlen=80)})
add('DepartureReceipt',{'departure_id':U,'employee_id':U,'effective_at':T,'assignments_closed':{'type':'integer','minimum':0},'pool_entries_created':{'type':'integer','minimum':0},'access_revocation_event_id':U})
add('PoolEvaluationRequest',{'as_of':T,'policy_version':string(maxlen=80),'dry_run':B,'customer_ids':arr(U,0,1000)},['as_of','policy_version','dry_run'])
add('PoolEvaluationResult',{'evaluation_id':U,'policy_version':string(maxlen=80),'dry_run':B,'rule_status':enum('proposed','approved'),'evaluated_count':{'type':'integer','minimum':0},'eligible_count':{'type':'integer','minimum':0},'blocked_count':{'type':'integer','minimum':0},'evidence_export_asset_id':U})
# M12
add('CommissionPolicyCreate',{'policy_code':string(maxlen=80),'effective_from':T,'effective_until':nullable(T),'base_pool_rate':nullable({'type':'string','pattern':r'^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$'}),'marketing_year_1':{'type':'string','const':'1.0'},'marketing_year_2':{'type':'string','const':'0.5'},'marketing_year_3_auto':{'type':'string','const':'0.25'},'assisted_marketing_share':{'type':'string','const':'0.3'},'assisted_sales_share':{'type':'string','const':'0.7'},'allocation_interpretation':enum('unresolved','marketing_decay_only','whole_pool_decay'),'age_anchor':enum('unresolved','first_paid_effective_start'),'service_lineage_rule':enum('unresolved','sticky_assisted_share','transaction_only_share'),'year3_assisted_marketing_rule':enum('unresolved','zero_unless_continuous_auto','approved_custom_rule'),'pool_policy_version':string(maxlen=80),'recognition_policy_version':string(maxlen=80),'rounding_mode':enum('ROUND_HALF_UP','ROUND_HALF_EVEN'),'rounding_scale':{'type':'integer','minimum':0,'maximum':6},'notes':string(maxlen=4000)},['policy_code','effective_from','base_pool_rate','marketing_year_1','marketing_year_2','marketing_year_3_auto','assisted_marketing_share','assisted_sales_share','allocation_interpretation','age_anchor','service_lineage_rule','year3_assisted_marketing_rule','pool_policy_version','recognition_policy_version','rounding_mode','rounding_scale'])
resource('CommissionPolicy',{'policy_code':string(maxlen=80),'state':enum('draft','proposed','approved','retired'),'effective_from':T,'effective_until':nullable(T),'base_pool_rate':nullable({'type':'string','pattern':r'^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$'}),'allocation_interpretation':enum('unresolved','marketing_decay_only','whole_pool_decay'),'age_anchor':enum('unresolved','first_paid_effective_start'),'service_lineage_rule':enum('unresolved','sticky_assisted_share','transaction_only_share'),'year3_assisted_marketing_rule':enum('unresolved','zero_unless_continuous_auto','approved_custom_rule'),'full_policy_asset_id':U,'policy_sha256':SHA,'approved_by':nullable(U),'approved_at':nullable(T),'unresolved_parameters':arr(string(maxlen=80),0,30)})
add('PolicyApprovalRequest',{'decision':enum('approve','reject'),'policy_sha256':SHA,'approved_base_pool_rate':{'type':'string','pattern':r'^(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)$'},'parameter_decision_evidence_asset_id':U,'effective_from':T,'reason_code':string(maxlen=80)})
add('CommissionRunRequest',{'policy_id':U,'period_start':D,'period_end':D,'revenue_cutoff_at':T,'recognition_ids':arr(U,0,10000),'assumption_overrides_asset_id':nullable(U)},['policy_id','period_start','period_end','revenue_cutoff_at'])
add('CommissionLine',{'recognition_id':U,'contract_id':U,'customer_id':U,'basis':ref('Money'),'base_pool_rate':DEC,'base_pool':ref('Money'),'marketing_share':DEC,'marketing_age_coefficient':DEC,'sales_share':DEC,'marketing_award':ref('Money'),'sales_award':ref('Money'),'company_retained':ref('Money'),'conversion_id':nullable(U),'marketing_attribution_id':nullable(U),'salesperson_id':nullable(U),'policy_id':U,'rule_trace_codes':arr(string(maxlen=80),1,30)},d='每列需滿足 marketing+sales+company=base_pool；退款額不能重複扣除預收負債退回。')
add('CommissionPreview',{'preview_id':U,'policy_id':U,'lines':arr(ref('CommissionLine'),0,10000),'live_payable':{'type':'boolean','const':False},'unapproved_assumptions':arr(string(maxlen=300),0,100),'control_total':ref('Money')})
resource('CommissionRun',{'policy_id':U,'period_start':D,'period_end':D,'revenue_cutoff_at':T,'state':enum('queued','calculating','calculated','review_pending','approved','rejected','exported','failed'),'basis_total':ref('Money'),'pool_total':ref('Money'),'marketing_total':ref('Money'),'sales_total':ref('Money'),'company_retained_total':ref('Money'),'prepared_by':U,'approved_by':nullable(U),'entry_count':{'type':'integer','minimum':0},'exception_count':{'type':'integer','minimum':0}})
resource('CommissionEntry',{'run_id':U,'calculation':ref('CommissionLine'),'recipient_type':enum('marketing','sales','company_retained'),'recipient_id':nullable(U),'amount':ref('Money'),'state':enum('calculated','approved','exported','adjusted'),'original_entry_id':nullable(U)})
add('CommissionAdjustmentRequest',{'original_entry_id':U,'refund_id':U,'attributable_recognition_id':U,'adjustment_amount':ref('Money'),'reason_code':string(maxlen=80),'evidence_asset_id':U})
resource('CommissionAdjustment',{'original_entry_id':U,'refund_id':U,'original_recipient_id':nullable(U),'amount':ref('Money'),'reason_code':string(maxlen=80),'state':enum('proposed','approved','disputed','exported'),'payroll_offset_allowed':B})
add('PayoutBatchRequest',{'approved_run_ids':arr(U,1,100),'payout_period':{'type':'string','pattern':'^\\d{4}-\\d{2}$'},'payroll_mapping_version':string(maxlen=80)})
resource('PayoutBatch',{'run_ids':arr(U,1,100),'state':enum('prepared','approval_pending','approved','exported','acknowledged','failed'),'total':ref('Money'),'line_count':{'type':'integer','minimum':0},'export_asset_id':nullable(U),'control_sha256':SHA})
# M13
add('ServiceCaseCreate',{'customer_id':nullable(U),'intake_channel':enum('web','line','email','phone','onsite'),'category':enum('application','contract','access','research_question','billing','refund','complaint','technical','other'),'subject':string(maxlen=200),'description':string(maxlen=12000),'original_content_asset_id':nullable(U),'contract_id':nullable(U),'publication_id':nullable(U),'payment_id':nullable(U),'priority':enum('normal','high','urgent')},['intake_channel','category','subject','description','priority'])
resource('ServiceCase',{'customer_id':nullable(U),'intake_channel':enum('web','line','email','phone','onsite'),'category':enum('application','contract','access','research_question','billing','refund','complaint','technical','other'),'subject':string(maxlen=200),'state':enum('new','triaged','assigned','waiting_customer','waiting_internal','resolved','closed','reopened'),'priority':enum('normal','high','urgent'),'assignee_id':nullable(U),'maintenance_owner_id':nullable(U),'first_response_due_at':T,'resolution_due_at':T,'sla_paused_reason':nullable(string(maxlen=80)),'linked_contract_id':nullable(U),'linked_publication_id':nullable(U),'linked_refund_id':nullable(U)})
add('CaseInteractionCreate',{'channel':enum('web','line','email','phone','onsite'),'direction':enum('inbound','outbound','internal'),'occurred_at':T,'content_asset_id':U,'summary':string(maxlen=4000),'publication_reference_ids':arr(U,0,20),'recording_consent_evidence_id':nullable(U)},['channel','direction','occurred_at','content_asset_id','summary'])
resource('CaseInteraction',{'case_id':U,'channel':enum('web','line','email','phone','onsite'),'direction':enum('inbound','outbound','internal'),'occurred_at':T,'recorded_by':U,'content_asset_id':U,'summary':string(maxlen=4000),'publication_reference_ids':arr(U,0,20)})
add('CaseEscalationRequest',{'target_queue':enum('analyst','finance','compliance','technical','service_supervisor'),'reason_code':string(maxlen=80),'due_at':T,'minimum_necessary_context_asset_id':U})
add('ResponseDraftRequest',{'knowledge_version':string(maxlen=80),'approved_source_refs':arr(U,1,30),'intent':enum('status','procedure','approved_report_explanation','billing_explanation'),'requested_tone':enum('professional','concise'),'model_policy_version':string(maxlen=80)})
resource('ResponseDraft',{'case_id':U,'state':enum('queued','drafted','requires_human','blocked','failed'),'text':nullable(string(maxlen=12000)),'citations':arr(U,0,30),'model_version':string(maxlen=100),'prompt_version':string(maxlen=100),'contains_new_investment_judgment':B,'send_authorized':{'type':'boolean','const':False}})
add('CaseResolutionRequest',{'resolution_code':string(maxlen=80),'resolution_summary':string(maxlen=4000),'customer_notice_evidence_id':U,'linked_refund_id':nullable(U),'follow_up_due_at':nullable(T)},['resolution_code','resolution_summary','customer_notice_evidence_id'])
# M14
add('InvoiceLineCreate',{'contract_service_id':U,'service_code':string(maxlen=80),'service_period_start':D,'service_period_end':D,'quantity':{'type':'integer','minimum':1},'untaxed_service_amount':ref('Money'),'tax_amount':ref('Money'),'tax_code':string(maxlen=40),'tax_mapping_version':string(maxlen=80)})
add('InvoiceCreate',{'customer_id':U,'contract_id':U,'invoice_date':D,'due_date':D,'lines':arr(ref('InvoiceLineCreate'),1,100),'invoice_type':enum('service','prepayment','adjustment')})
resource('Invoice',{'customer_id':U,'contract_id':U,'invoice_date':D,'due_date':D,'state':enum('draft','issued','partially_paid','paid','voided','credited'),'untaxed_service_amount':ref('Money'),'tax_amount':ref('Money'),'total_amount':ref('Money'),'lines':arr(ref('InvoiceLineCreate'),1,100)})
add('PaymentIntentCreate',{'application_id':U,'invoice_id':nullable(U),'amount':ref('Money'),'method':enum('card','bank_transfer','approved_wallet'),'return_route':enum('application_progress','member_billing')},['application_id','amount','method','return_route'])
resource('PaymentIntent',{'application_id':U,'provider_intent_ref':string(maxlen=160),'state':enum('pending','authorized','paid','failed','expired'),'checkout_url':{'type':'string','format':'uri','pattern':'^https://'},'amount':ref('Money'),'expires_at':T})
add('PaymentCallback',{'event_id':U,'provider_transaction_ref':string(maxlen=200),'payment_intent_id':U,'status':enum('authorized','paid','failed','disputed'),'amount':ref('Money'),'provider_occurred_at':T,'evidence_asset_id':U})
add('SettlementImportRequest',{'provider':string(maxlen=80),'batch_ref':string(maxlen=160),'settlement_date':D,'statement_asset_id':U,'statement_sha256':SHA,'declared_gross':ref('Money'),'declared_fee':ref('Money'),'declared_net':ref('Money'),'declared_count':{'type':'integer','minimum':0},'format_version':string(maxlen=80)})
resource('SettlementImport',{'provider':string(maxlen=80),'batch_ref':string(maxlen=160),'state':enum('received','parsing','matched','exceptions','rejected'),'settlement_date':D,'matched_count':{'type':'integer','minimum':0},'exception_count':{'type':'integer','minimum':0},'gross':ref('Money'),'fee':ref('Money'),'net':ref('Money')})
resource('ReconciliationItem',{'settlement_id':U,'payment_id':nullable(U),'difference_type':enum('unmatched_payment','unmatched_settlement','amount_mismatch','duplicate','fee_mismatch','timing'),'expected_amount':ref('Money'),'actual_amount':ref('Money'),'state':enum('open','investigating','resolved','escalated'),'owner_id':nullable(U),'resolved_by':nullable(U)})
add('ReconciliationResolution',{'decision':enum('match','correct_mapping','accept_timing','reject','escalate'),'payment_id':nullable(U),'reason_code':string(maxlen=80),'evidence_asset_id':U},['decision','reason_code','evidence_asset_id'])
add('RevenueRunRequest',{'period_start':D,'period_end':D,'recognition_policy_version':string(maxlen=80),'cutoff_at':T,'dry_run':B})
resource('RevenueRun',{'period_start':D,'period_end':D,'recognition_policy_version':string(maxlen=80),'state':enum('queued','calculated','review_pending','approved','posted','failed'),'recognized_untaxed_total':ref('Money'),'deferred_remaining':ref('Money'),'entry_count':{'type':'integer','minimum':0},'exception_count':{'type':'integer','minimum':0},'journal_ids':arr(U,0,10000)})
add('RefundAllocation',{'invoice_line_ref':U,'recognition_id':nullable(U),'unearned_amount':ref('Money'),'earned_reversal_amount':ref('Money'),'tax_adjustment_amount':ref('Money')},d='unearned_amount與earned_reversal_amount分開；獎金只扣已進計獎基礎的earned reversal。')
add('RefundCreate',{'customer_id':U,'contract_id':U,'original_payment_id':U,'case_id':nullable(U),'reason_code':string(maxlen=80),'requested_amount':ref('Money'),'allocations':arr(ref('RefundAllocation'),1,100),'calculation_policy_version':string(maxlen=80),'customer_request_evidence_id':U},['customer_id','contract_id','original_payment_id','reason_code','requested_amount','allocations','calculation_policy_version','customer_request_evidence_id'])
resource('Refund',{'customer_id':U,'contract_id':U,'original_payment_id':U,'state':enum('requested','calculated','approval_pending','approved','submitted','confirmed','failed','reconciliation_required','accounting_adjusted','closed','rejected'),'requested_amount':ref('Money'),'approved_amount':nullable(ref('Money')),'allocations':arr(ref('RefundAllocation'),1,100),'prepared_by':U,'approved_by':nullable(U),'provider_refund_ref':nullable(string(maxlen=200)),'confirmed_at':nullable(T)})
add('RefundExecutionRequest',{'approval_record_id':U,'original_payment_id':U,'provider_idempotency_key':string(maxlen=128),'last_provider_status':enum('not_submitted','confirmed_absent')})
add('RefundCallback',{'event_id':U,'refund_id':U,'provider_refund_ref':string(maxlen=200),'status':enum('confirmed','failed','pending'),'amount':ref('Money'),'provider_occurred_at':T,'evidence_asset_id':U})
add('AccountingExportRequest',{'period_start':D,'period_end':D,'journal_ids':arr(U,1,10000),'account_mapping_version':string(maxlen=80),'tax_mapping_version':string(maxlen=80),'export_format':enum('csv_v1','json_v1')})
resource('AccountingExport',{'state':enum('prepared','approved','exported','partially_acknowledged','acknowledged','failed'),'account_mapping_version':string(maxlen=80),'tax_mapping_version':string(maxlen=80),'debit_total':ref('Money'),'credit_total':ref('Money'),'entry_count':{'type':'integer','minimum':0},'export_asset_id':nullable(U),'export_sha256':nullable(SHA),'acknowledgement_ref':nullable(string(maxlen=160))})
add('ExportAckRequest',{'event_id':U,'external_batch_ref':string(maxlen=160),'export_sha256':SHA,'status':enum('accepted','partial','rejected'),'accepted_journal_ids':arr(U,0,10000),'rejected_journal_ids':arr(U,0,10000),'rejection_evidence_asset_id':nullable(U)})
resource('AuditEvent',{'actor_id':nullable(U),'actor_type':enum('staff','customer','service'),'action':string(maxlen=160),'aggregate_type':string(maxlen=80),'aggregate_id':U,'occurred_at':T,'correlation_id':U,'outcome':enum('success','denied','failed'),'evidence_asset_ids':arr(U,0,50),'before_sha256':nullable(SHA),'after_sha256':nullable(SHA),'classification':enum('internal','customer_confidential','aml_restricted','research_confidential')})
add('EvidenceExportRequest',{'scope':enum('customer','application','contract','publication','commission_run','refund'),'subject_id':U,'purpose':enum('internal_audit','regulatory_exam','complaint_investigation','litigation'),'include_restricted_aml':B,'approval_record_id':U,'expires_at':T})
resource('EvidenceExport',{'scope':string(maxlen=80),'subject_id':U,'state':enum('queued','building','ready','failed','expired'),'manifest_asset_id':nullable(U),'export_asset_id':nullable(U),'manifest_sha256':nullable(SHA),'expires_at':T,'requester_id':U})
add('LegalHoldRequest',{'scope':enum('customer','contract','publication','commission_run','refund','record_set'),'subject_ids':arr(U,1,1000),'reason_code':string(maxlen=80),'authority_evidence_asset_id':U,'review_due_at':T})
resource('LegalHold',{'scope':string(maxlen=80),'subject_ids':arr(U,1,1000),'state':enum('active','released'),'reason_code':string(maxlen=80),'authority_evidence_asset_id':U,'created_by':U,'review_due_at':T})
add('ControlExceptionRequest',{'control_code':string(maxlen=80),'severity':enum('low','medium','high','critical'),'description':string(maxlen=4000),'evidence_asset_ids':arr(U,1,50),'remediation_owner_id':U,'due_at':T})
resource('ControlException',{'control_code':string(maxlen=80),'severity':enum('low','medium','high','critical'),'state':enum('open','remediating','ready_for_review','closed','reopened'),'remediation_owner_id':U,'due_at':T,'closed_by':nullable(U),'closure_evidence_id':nullable(U)})
# Core query and approval loops: no module must require direct DB edits for operations.
op('M01','get','/customer-merges','list_customer_merges',None,'MergeCasePage','列出等待異人覆核的身分合併案件','cms_supervisor')
op('M01','get','/customer-merges/{merge_case_id}','get_customer_merge',None,'MergeCase','查詢合併證據與原始主檔關係','cms_operator,cms_supervisor')
op('M03','get','/documents/{asset_id}/status','get_document_status',None,'DocumentStatus','查詢本人或受權文件的隔離掃描狀態','kyc_reviewer','customer')
op('M06','get','/revision-requests/{revision_id}','get_revision_request',None,'Revision','查詢Servant非同步修訂進度及版本','analyst,analyst_reviewer')
op('M10','get','/crm/opportunities','list_opportunities',None,'OpportunityPage','依owner與客戶scope檢視機會清單','sales,sales_supervisor')
op('M10','get','/crm/opportunities/{opportunity_id}','get_opportunity',None,'Opportunity','查詢具體業務機會與狀態','sales,sales_supervisor')
op('M11','get','/customers/{customer_id}/ownership-history','get_ownership_history',None,'MaintenanceAssignmentPage','查看有效期間與交接歷史，不以現owner覆寫舊獎金','sales,sales_supervisor')
op('M12','get','/commissions/payout-batches/{batch_id}','get_commission_payout_batch',None,'PayoutBatch','查詢薪酬匯出、核准及接收狀態','payroll_operator,commission_approver')
op('M12','post','/commissions/payout-batches/{batch_id}/approval','approve_commission_payout_batch','Decision','PayoutBatch','薪酬輸出由不同人核准與原run控制總額對比','commission_approver',cas=True)
op('M12','post','/commissions/payout-batches/{batch_id}/acknowledgement','acknowledge_commission_payout_batch','PayoutAckRequest','PayoutBatch','記錄薪酬接收結果，部分成功不重複發放','payroll_adapter','integration',202)
op('M13','get','/service-response-drafts/{draft_id}','get_service_response_draft',None,'ResponseDraft','取回LLM草稿及引用供客服覆核','service_agent')
op('M14','get','/finance/revenue-runs/{run_id}','get_revenue_run',None,'RevenueRun','檢視收入攤認批次與控制總額','finance_operator,finance_checker')
op('M14','post','/finance/revenue-runs/{run_id}/approval','approve_revenue_run','RevenueApprovalRequest','RevenueRun','異人核准攤認批次，核准後才能過帳及產生獎金基礎','finance_checker',cas=True)
op('M14','get','/finance/refunds/{refund_id}','get_refund',None,'Refund','查詢退款、對帳與收入沖回進度','finance_operator,service_agent')
op('M14','get','/finance/accounting-exports/{export_id}','get_accounting_export',None,'AccountingExport','查詢會計匯出hash與部分接收回覆','finance_operator,finance_checker')
op('M14','get','/audit/evidence-exports/{export_id}','get_evidence_export',None,'EvidenceExport','查詢稽核匯出與到期票證流程','internal_auditor')
op('M14','post','/audit/legal-holds/{hold_id}/release','release_legal_hold','LegalHoldReleaseRequest','LegalHold','依權威解除證據保全，不能由排程自行解除','compliance_supervisor',cas=True)
op('M14','post','/audit/control-exceptions/{exception_id}/actions','act_on_control_exception','ControlExceptionActionRequest','ControlException','改善承辦與獨立稽核覆核保留歷程','control_remediation_owner,internal_auditor',cas=True)
add('DocumentStatus',{'asset_id':U,'scan_status':enum('pending','clean','rejected'),'rejection_code':nullable(enum('malware','mime_mismatch','oversized','invalid_format')),'scanned_at':nullable(T),'classification':enum('customer_confidential','aml_restricted','research_confidential','internal')})
add('PayoutAckRequest',{'event_id':U,'external_batch_ref':string(maxlen=160),'control_sha256':SHA,'status':enum('accepted','partial','rejected'),'accepted_entry_ids':arr(U,0,10000),'rejected_entry_ids':arr(U,0,10000),'acknowledgement_evidence_id':U})
add('RevenueApprovalRequest',{'decision':enum('approve','reject'),'calculation_manifest_sha256':SHA,'reason_code':string(maxlen=80),'review_evidence_id':U})
S['RevenueRun']['properties']['calculation_manifest_sha256']=SHA
S['RevenueRun']['required'].append('calculation_manifest_sha256')
add('LegalHoldReleaseRequest',{'reason_code':string(maxlen=80),'release_authority_evidence_id':U,'independent_approver_id':U,'effective_at':T})
add('ControlExceptionActionRequest',{'action':enum('add_remediation_evidence','request_closure','approve_closure','reject_closure','reopen'),'reason_code':string(maxlen=80),'evidence_asset_ids':arr(U,1,50),'note':string(maxlen=4000)},['action','reason_code','evidence_asset_ids'])
op('M13','post','/service-cases/{case_id}/responses','send_service_response','ServiceResponseRequest','ServiceResponse','核定精確草稿或低風險固定模板後由本平台送出客服回覆','service_agent,service_supervisor,service_response_service','staff_or_service',202,True)
op('M13','get','/service-responses/{response_id}','get_service_response',None,'ServiceResponse','查詢客服回覆之排程、接受、送達或未知結果','service_agent,service_supervisor')
op('M13','post','/webhooks/service-response-receipts','receive_service_response_receipt','ServiceResponseReceipt','CallbackReceipt','接收客服回覆渠道receipt，不能用outbound紀錄冒充送達','service_messaging_adapter','integration',202)
add('ServiceResponseRequest',{'draft_id':U,'draft_content_sha256':SHA,'channel_binding_id':U,'channel':enum('web','line','email','sms'),'approval_mode':enum('human_reviewed','approved_low_risk_template'),'approval_id':U,'template_version':string(maxlen=80),'publication_reference_ids':arr(U,0,20),'valid_until':T},['draft_id','draft_content_sha256','channel_binding_id','channel','approval_mode','approval_id','template_version','publication_reference_ids','valid_until'])
resource('ServiceResponse',{'case_id':U,'draft_id':U,'content_sha256':SHA,'channel_binding_id':U,'channel':enum('web','line','email','sms'),'approval_mode':enum('human_reviewed','approved_low_risk_template'),'approval_id':U,'state':enum('queued','accepted','sent','delivered','failed','unknown','cancelled','expired'),'provider_message_ref':nullable(string(maxlen=200)),'accepted_at':nullable(T),'sent_at':nullable(T),'delivered_at':nullable(T),'read_at':nullable(T),'valid_until':T,'outbound_interaction_id':nullable(U)})
add('ServiceResponseReceipt',{'event_id':U,'response_id':U,'provider_message_ref':string(maxlen=200),'provider_status':enum('accepted','sent','delivered','failed','read'),'provider_occurred_at':T,'provider_receipt_asset_id':U})
op('M13','post','/service-response-drafts/{draft_id}/approval','approve_service_response_draft','ServiceResponseApprovalRequest','ServiceResponseApproval','對固定草稿做人工核定或可信固定模板規則核定，產生server approval record','service_agent,service_supervisor,service_response_service','staff_or_service',201,True)
add('ServiceResponseApprovalRequest',{'draft_content_sha256':SHA,'decision':enum('approve','reject'),'approval_mode':enum('human_reviewed','approved_low_risk_template'),'template_version':string(maxlen=80),'policy_version':string(maxlen=80),'reason_code':string(maxlen=80),'publication_reference_ids':arr(U,0,20),'valid_until':T})
resource('ServiceResponseApproval',{'draft_id':U,'draft_content_sha256':SHA,'decision':enum('approve','reject'),'approval_mode':enum('human_reviewed','approved_low_risk_template'),'reviewer_id':U,'template_version':string(maxlen=80),'policy_version':string(maxlen=80),'evidence_asset_id':U,'approved_at':T,'valid_until':T})
for schema_name in ('ChannelBindingRequest','ChannelBinding','CommunicationPreferenceRequest','CommunicationPreference'):
 channel=S[schema_name]['properties']['channel']
 if 'web' not in channel['enum']: channel['enum'].append('web')
# Incremental catalog fixes requested by module author.
op('M01','patch','/customers/{customer_id}','update_customer','CustomerUpdate','Customer','本人／受權客服更新白名單資料，驗證聯絡資料以proof另核','cms_operator,service_agent','customer',cas=True)
add('CustomerUpdate',{'display_name':string(maxlen=100),'preferred_locale':enum('zh-TW','en'),'contact_update_proof_ref':U},[])
S['CustomerUpdate']['minProperties']=1
S['RecommendationVersionCreate']['properties']['source_candidate_id']=U
S['RecommendationVersionCreate']['properties']['supporting_report_version_ids']['minItems']=0
S['RecommendationVersionCreate']['required'].append('source_candidate_id')
S['RecommendationVersionCreate']['description']='草稿可先引用候選並保留空report版本集合；建立報告版本時原子綁定，freeze以前必須有完整投顧報告依據。'
# Reject/request-information decisions may occur before all due diligence exists.
S['CompanyAcceptanceRequest']['required']=['decision','package_version_id','customer_signature_id','reason_code']
S['CompanyAcceptanceRequest']['allOf']=[{'if':{'properties':{'decision':{'const':'accept'}},'required':['decision']},'then':{'required':['kyc_submission_id','suitability_assessment_id','business_clearance_id','intended_effective_at','payment_condition_version']}}]
S['CompanyAcceptanceRequest']['description']='accept必須綁完整且有效的KYC/適合度/clearance；reject或request_changes可在尚未完成審查時記錄，不得要求偽造不存在的核准證據。'
# A submitted answer must carry exactly the value corresponding to its declared type.
S['QuestionAnswer']={'oneOf':[
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'text','type':'string'},'text_value':{'type':'string','minLength':1,'maxLength':2000}}),
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'choice','type':'string'},'choice_codes':arr(string(maxlen=80),1,1)}),
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'multi_choice','type':'string'},'choice_codes':arr(string(maxlen=80),1,30)}),
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'number','type':'string'},'number_value':DEC}),
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'boolean','type':'string'},'boolean_value':B}),
 obj({'question_id':string(maxlen=80),'answer_type':{'const':'date','type':'string'},'date_value':D})
], 'description':'表單版次另限制每question_id之允許型別、選項及必填；不能使用空值假裝已回答。'}
# Failure callbacks do not have fictitious signed bytes.
for name, discriminator, success in [('CustomerSignatureCallback','result','signed'),('AnalystSignatureCallback','verification_result','verified')]:
 for field in ['signed_asset_id','signed_file_sha256','provider_signed_at','signed_at','verification_evidence_asset_id','signer_subject_ref']:
  if field in S[name]['required']: S[name]['required'].remove(field)
 needed=[f for f in ['signed_asset_id','signed_file_sha256','provider_signed_at','signed_at','verification_evidence_asset_id','signer_subject_ref'] if f in S[name]['properties']]
 S[name]['allOf']=[{'if':{'properties':{discriminator:{'const':success}},'required':[discriminator]},'then':{'required':needed}}]
# All named list responses use stable cursor pagination.
for row in OPS:
 if row['response'].endswith('Page') and row['response'] not in S:
  page(row['response'], 'MemberFeedItem' if row['response']=='MemberFeedPage' else row['response'][:-4])
# Closed envelope for RFC9457-style domain problems.
add('Problem',{'type':{'type':'string','format':'uri'},'title':string(maxlen=200),'status':{'type':'integer','minimum':400,'maximum':599},'detail':string(maxlen=2000),'instance':string(maxlen=500),'code':string(maxlen=100),'correlation_id':U,'retryable':B,'field_errors':arr(ref('FieldError'),0,100),'current_version':nullable(POS)},['type','title','status','code','correlation_id','retryable'])
add('FieldError',{'field':string(maxlen=200),'code':string(maxlen=100),'message':string(maxlen=300)})
# OpenAPI assembly: every command has auth, explicit resource schema, replay and conflict behavior.
parameters={
 'CorrelationId':{'in':'header','name':'X-Correlation-Id','required':False,'schema':U,'description':'未提供時由API產生並回傳；不得承載個資。'},
 'IdempotencyKey':{'in':'header','name':'Idempotency-Key','required':True,'schema':{'type':'string','minLength':8,'maxLength':128,'pattern':'^[A-Za-z0-9._:-]+$'},'description':'依tenant+principal+operation+key唯一；相同payload重送回原結果，異payload回409。敏感外部支付key保存到最長業務重送窗口，已完成金融命令不得因cache到期重複執行。'},
 'IfMatch':{'in':'header','name':'If-Match','required':True,'schema':{'type':'string','pattern':'^"[1-9][0-9]*"$'},'description':'強ETag，例如 "7"；不足回428，過時回412。以操作路徑主aggregate的version做CAS。'},
 'Cursor':{'in':'query','name':'cursor','required':False,'schema':{'type':'string','maxLength':1024}},
 'Limit':{'in':'query','name':'limit','required':False,'schema':{'type':'integer','minimum':1,'maximum':200,'default':50}},
 'AsOf':{'in':'query','name':'as_of','required':False,'schema':T,'description':'首次query建立snapshot time；續頁游標綁定，不可偷偷換filter/tenant。'},
 'WebhookTimestamp':{'in':'header','name':'X-Webhook-Timestamp','required':True,'schema':{'type':'string','pattern':'^[0-9]{10}$'},'description':'簽署的Unix seconds；允許±300秒，可調但版本留痕。'},
 'WebhookNonce':{'in':'header','name':'X-Webhook-Nonce','required':True,'schema':{'type':'string','minLength':16,'maxLength':128}},
 'IntegrationKeyId':{'in':'header','name':'X-Integration-Key-Id','required':True,'schema':{'type':'string','minLength':8,'maxLength':128}}
}
error_descriptions={400:'輸入語法不正確',401:'身分／回呼簽章無效',403:'權限、資格或SoD不符',404:'不存在或不在可見授權範圍',409:'狀態衝突、重放payload不一致或規則未核准',412:'If-Match版本過時，請重讀比較',422:'資料與業務規則未滿足',428:'缺少If-Match或必要冪等前提',429:'超過速率限制，依Retry-After',503:'依賴服務不可用，不能假裝空資料或完成'}
responses={}
for code,description in error_descriptions.items():
 responses['Error'+str(code)]={'description':description,'headers':{'X-Correlation-Id':{'schema':U}},'content':{'application/problem+json':{'schema':ref('Problem')}}}
 if code in (429,503): responses['Error'+str(code)]['headers']['Retry-After']={'schema':{'type':'integer','minimum':1}}
paths={}
for row in OPS:
 oid=row['operation_id']; method=row['method']; auth=row['auth']; mutation=method!='get'
 params=[{'$ref':'#/components/parameters/CorrelationId'}]
 for p in re.findall(r'{([^}]+)}',row['path']): params.append({'in':'path','name':p,'required':True,'schema':U})
 if mutation: params.append({'$ref':'#/components/parameters/IdempotencyKey'})
 if row['compare_and_set']: params.append({'$ref':'#/components/parameters/IfMatch'})
 if row['response'].endswith('Page'):
  params += [{'$ref':'#/components/parameters/'+p} for p in ['Cursor','Limit','AsOf']]
  params.append({'in':'query','name':'state','required':False,'schema':string(maxlen=80),'description':'只允許該資源已定義state；不允許任意SQL語法。'})
 if auth=='integration':
  params += [{'$ref':'#/components/parameters/'+p} for p in ['WebhookTimestamp','WebhookNonce','IntegrationKeyId']]
  security=[{'integrationSignature':[]}]
 elif auth=='service': security=[{'serviceBearer':[]}]
 elif auth=='staff_or_service': security=[{'staffBearer':[]},{'serviceBearer':[]}]
 elif auth=='customer': security=[{'customerBearer':[]}]+([] if row['roles']==['member'] else [{'staffBearer':[]}])
 else: security=[{'staffBearer':[]}]
 d={'operationId':oid,'summary':row['summary'],'tags':[row['module']],'security':security,'parameters':params,
    'x-module':row['module'],'x-allowed-roles':row['roles'],'x-resource-ownership':('self_or_authorized_staff' if auth=='customer' and row['roles']!=['member'] else 'self' if auth=='customer' else 'tenant_and_scope'),
    'x-audit-action':row['module'].lower()+'.'+oid,'x-idempotency-required':mutation,
    'description':'本平台目標契約；外部系統端點需透過adapter映射，不能假定供應商已實作。'+(' 此命令以路徑主aggregate版本進行compare-and-set。' if row['compare_and_set'] else '')+(' 非人工操作，服務身分必須持有明確scope與因果事件引用。' if auth=='service' else ''),
    'responses':{str(row['status']):{'description':'成功（202僅表示已持久接收，需讀取資源／事件確認完成）' if row['status']==202 else '成功','headers':{'X-Correlation-Id':{'schema':U},'ETag':{'schema':{'type':'string','pattern':'^"[1-9][0-9]*"$'}}},'content':{'application/json':{'schema':ref(row['response'])}}},**{str(k):{'$ref':'#/components/responses/Error'+str(k)} for k in error_descriptions}}}
 if mutation:
  d['requestBody']={'required':True,'content':{'application/json':{'schema':ref(row['request'])}}}
  d['responses'][str(row['status'])]['headers']['Idempotency-Replayed']={'schema':B}
 paths.setdefault(row['path'],{})[method]=d
for row in OPS:
 for key in ['request','response']:
  if row[key] and row[key] not in S: raise ValueError((row['operation_id'],row[key]))
spec={'openapi':'3.1.0','jsonSchemaDialect':'https://json-schema.org/draft/2020-12/schema','info':{'title':'龍好智慧投顧營運平台 — 14 模組完整 API 契約','version':'1.0.0','description':'完整範圍平行開發之契約基準。此版本為設計契約與離線mock，尚非生產功能；無MVP縮減。正式上線須14模組、外部adapter與控制完整通過驗收。'},'servers':[{'url':'https://ops.example.invalid/api/v1','description':'正式網域待部署設定；example.invalid不得當真實服務'},{'url':'http://127.0.0.1:8765/api/v1','description':'離線合約mock；不是營運後端'}],'tags':[{'name':f'M{i:02}','description':n} for i,n in enumerate(['客戶與身分／申辦','契約與服務權限','KYC與適合度','AML/CFT','Pantheon候選接收','Agora/Servant審查協作','研究報告與簽章','簽章後自動發布','LegendFlow與訊息投遞','CRM行銷漏斗與業務','維護歸屬與公司池','獎金計算與結算','線上線下客服','財會與內控稽核'],1)],'paths':paths,'components':{'securitySchemes':{'staffBearer':{'type':'http','scheme':'bearer','bearerFormat':'JWT','description':'OIDC aud=longgood-ops，MFA、sub、tenant及role scopes由可信issuer提供。'},'customerBearer':{'type':'http','scheme':'bearer','bearerFormat':'JWT','description':'LegendFlow BFF驗證之customer subject；server端映射customer_id，不能相信body任意id。'},'serviceBearer':{'type':'http','scheme':'bearer','bearerFormat':'JWT','description':'短效workload identity token；每服務獨立aud/scope，禁止共用staff token。'},'integrationSignature':{'type':'apiKey','in':'header','name':'X-Webhook-Signature','description':'HMAC-SHA256(key, timestamp+newline+nonce+newline+METHOD+newline+path+newline+SHA256(raw_body))；hex編碼constant-time compare。供應商原生簽章由專屬adapter驗證後轉標準內部信封。'}},'parameters':parameters,'responses':responses,'schemas':S}}
(ROOT/'api/openapi.yaml').write_text(yaml.safe_dump(spec,allow_unicode=True,sort_keys=False,width=120))
(ROOT/'api/operation-catalog.json').write_text(json.dumps(OPS,ensure_ascii=False,indent=2)+'\n')
(ROOT/'api/schema-index.json').write_text(json.dumps({'openapi_version':'3.1.0','contract_version':'1.0.0','schema_count':len(S),'schemas':sorted(S)},ensure_ascii=False,indent=2)+'\n')
print('schemas',len(S),'paths',len(paths),'operations',len(OPS))
# Complete versioned event contracts. Payloads contain references, never names/documents/AML narratives.
EV=[]
def evt(name,producer,consumers,aggregate,fields,classification='internal',purpose=''):
 EV.append(dict(event_type=name,schema_version='1.0.0',producer=producer,consumers=consumers.split(','),aggregate_type=aggregate,payload_properties=fields,classification=classification,purpose=purpose))
evt('customer.created','M01','M10,M13','customer',{'customer_id':U,'marketing_attribution_id':nullable(U)},'customer_confidential','建立CRM主檔投影，不帶直接個資。')
evt('customer.identity_linked','M01','M09','customer',{'customer_id':U,'identity_id':U,'provider':enum('legendflow','line','email','phone')},'customer_confidential')
evt('application.created','M01','M02,M10','application',{'application_id':U,'customer_id':U,'package_version_id':U,'intent':enum('new','renewal','upgrade','reactivation')},'customer_confidential')
evt('application.customer_signed','M02','M01,M03,M04,M10','application',{'application_id':U,'customer_signature_id':U,'package_version_id':U,'document_sha256':SHA,'customer_signed_at':T},'customer_confidential')
evt('application.company_decided','M02','M01,M10,M14','application',{'application_id':U,'acceptance_id':U,'decision':enum('accept','reject','request_changes'),'contract_id':nullable(U)},'customer_confidential')
evt('contract.effective','M02','M10,M11,M14','contract',{'contract_id':U,'customer_id':U,'effective_at':T,'service_starts_at':T,'service_ends_at':T},'customer_confidential')
evt('contract.terminated','M02','M09,M10,M11,M14','contract',{'contract_id':U,'customer_id':U,'terminated_at':T,'refund_evaluation_required':B},'customer_confidential')
evt('entitlement.changed','M02','M08,M09,M10','entitlement',{'entitlement_id':U,'customer_id':U,'contract_id':U,'state':enum('locked','eligible','active','suspended','expired','revoked'),'decision_id':U,'valid_until':T},'customer_confidential')
evt('kyc.decision_recorded','M03','M02,M04','kyc_submission',{'submission_id':U,'application_id':U,'decision':enum('approved','rejected','incomplete'),'next_review_due_at':nullable(T)},'customer_confidential')
evt('suitability.assessed','M03','M02','suitability_assessment',{'assessment_id':U,'application_id':U,'state':enum('suitable','conditional','not_suitable'),'rule_version':string(maxlen=80)},'customer_confidential')
evt('aml.case_changed','M04','M04','aml_case',{'case_id':U,'screening_id':U,'restricted_record_version':POS},'aml_restricted','僅AML隔離topic可訂閱；一般CRM不見case存在與內容。')
evt('business_clearance.changed','M04','M02','business_clearance',{'clearance_id':U,'application_id':U,'decision':enum('pending','approved','denied','expired'),'expires_at':nullable(T)},'customer_confidential','安全摘要不含名單命中、申報或調查原因。')
evt('research.candidate_received','M05','M05','research_candidate',{'candidate_id':U,'external_event_id':U,'artifact_content_sha256':SHA,'origin':enum('persona_autonomous','analyst_initiated')},'research_confidential')
evt('research.candidate_validated','M05','M06','research_candidate',{'candidate_id':U,'review_case_id':U,'artifact_content_sha256':SHA,'validation_result':enum('accepted','quarantined','rejected')},'research_confidential')
evt('review.revision_requested','M06','M06','review_case',{'review_case_id':U,'revision_id':U,'conversation_id':U,'base_content_sha256':SHA},'research_confidential')
evt('review.revision_received','M06','M07','review_case',{'review_case_id':U,'revision_id':U,'candidate_id':U,'result_content_sha256':SHA,'stale':B},'research_confidential')
evt('report.version_frozen','M07','M07','report_version',{'report_version_id':U,'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'review_decision_ids':arr(U,1,10)},'research_confidential')
evt('report.signature_verified','M07','M08','report_version',{'report_version_id':U,'signature_id':U,'report_content_sha256':SHA,'bundle_manifest_sha256':SHA,'signed_file_sha256':SHA,'verified_at':T,'valid_until':T},'research_confidential','同交易持久化 verified signature 與 outbox；M08驗證簽章狀態後自動發布，無第二人工發布。')
evt('report.signature_revoked','M07','M08,M09','signature',{'signature_id':U,'report_version_id':U,'revocation_id':U,'effective_at':T},'research_confidential')
evt('publication.created','M08','M09','publication',{'publication_id':U,'report_version_id':U,'signature_id':U,'audience_snapshot_id':U,'bundle_manifest_sha256':SHA,'valid_until':T},'research_confidential')
evt('publication.withdrawn','M08','M09,M13','publication',{'publication_id':U,'withdrawal_record_id':U,'effective_at':T,'notify_recipients':B},'research_confidential')
evt('publication.corrected','M08','M09,M13','publication',{'publication_id':U,'replacement_publication_id':U,'replacement_report_version_id':U},'research_confidential')
evt('delivery.status_changed','M09','M08,M13','delivery',{'delivery_id':U,'publication_id':U,'customer_id':U,'channel':enum('legendflow','line','sms','email'),'state':enum('queued','accepted','sent','delivered','failed','expired','suppressed','unknown'),'receipt_id':nullable(U),'read_at':nullable(T)},'customer_confidential')
evt('crm.opportunity_changed','M10','M10','opportunity',{'opportunity_id':U,'customer_id':U,'state':enum('open','qualified','proposal','awaiting_customer','converted','lost','suppressed'),'owner_id':U},'customer_confidential')
evt('crm.conversion_decided','M10','M12','conversion',{'conversion_id':U,'contract_id':U,'decision':enum('approved','rejected','disputed','reversed'),'approved_salesperson_id':nullable(U),'evidence_snapshot_id':U},'customer_confidential')
evt('crm.suppression_changed','M10','M09,M10','suppression',{'suppression_id':U,'customer_id':U,'scope':enum('marketing_contact','sales_contact','all_nonessential_contact'),'active':B},'customer_confidential')
evt('ownership.assignment_changed','M11','M10,M12,M13','maintenance_assignment',{'assignment_id':U,'customer_id':U,'salesperson_id':nullable(U),'effective_at':T,'state':enum('active','released','departure','superseded')},'customer_confidential')
evt('ownership.pool_entered','M11','M10','pool_entry',{'pool_entry_id':U,'customer_id':U,'entry_reason':enum('unassigned_web','employee_departure','paid_service_lapse','manual_release'),'policy_version':string(maxlen=80)},'customer_confidential')
evt('ownership.employee_departed','M11','M01,M10,M11','employee',{'employee_id':U,'departure_id':U,'effective_at':T},'internal')
evt('commission.run_calculated','M12','M12','commission_run',{'run_id':U,'policy_id':U,'entry_count':{'type':'integer','minimum':0},'calculation_manifest_sha256':SHA},'internal')
evt('commission.run_approved','M12','M14','commission_run',{'run_id':U,'policy_id':U,'approval_id':U,'ledger_manifest_sha256':SHA},'internal')
evt('commission.adjustment_recorded','M12','M14','commission_adjustment',{'adjustment_id':U,'original_entry_id':U,'refund_id':U,'original_recipient_id':nullable(U)},'internal')
evt('support.case_opened','M13','M10,M13','service_case',{'case_id':U,'customer_id':nullable(U),'category':enum('application','contract','access','research_question','billing','refund','complaint','technical','other'),'priority':enum('normal','high','urgent')},'customer_confidential')
evt('support.case_escalated','M13','M13','service_case',{'case_id':U,'target_queue':enum('analyst','finance','compliance','technical','service_supervisor'),'due_at':T},'customer_confidential')
evt('support.case_resolved','M13','M10','service_case',{'case_id':U,'customer_id':nullable(U),'resolution_record_id':U},'customer_confidential')
evt('finance.payment_confirmed','M14','M02,M10','payment',{'payment_id':U,'application_id':U,'invoice_id':nullable(U),'confirmed_at':T,'evidence_snapshot_id':U},'customer_confidential')
evt('finance.revenue_recognized','M14','M12','revenue_recognition',{'recognition_id':U,'contract_id':U,'contract_service_id':U,'policy_version':string(maxlen=80),'service_period_start':D,'service_period_end':D,'posted_journal_id':U},'internal')
evt('finance.refund_confirmed','M14','M02,M10,M12,M13','refund',{'refund_id':U,'contract_id':U,'original_payment_id':U,'allocation_snapshot_id':U,'confirmed_at':T},'customer_confidential')
evt('finance.accounting_export_acknowledged','M14','M14','accounting_export',{'export_id':U,'status':enum('accepted','partial','rejected'),'acknowledgement_id':U,'export_sha256':SHA},'internal')
evt('audit.legal_hold_changed','M14','M14','legal_hold',{'legal_hold_id':U,'state':enum('active','released'),'scope':enum('customer','contract','publication','commission_run','refund','record_set')},'internal')
evt('support.response_status_changed','M13','M13','service_response',{'response_id':U,'case_id':U,'state':enum('queued','accepted','sent','delivered','failed','unknown','cancelled','expired'),'receipt_id':nullable(U)},'customer_confidential')
evt('customer.profile_updated','M01','M03,M04','customer',{'customer_id':U,'changed_field_codes':arr(enum('display_name','contact_channel','identity_evidence','residency','beneficial_owner'),1,5),'profile_evidence_ref':U},'customer_confidential','僅欄位代碼，不攜直接個資；必要時觸發重評。')
evt('customer.consent_changed','M01','M09,M10','customer',{'customer_id':U,'consent_id':U,'purpose':enum('privacy','electronic_signing','marketing','service_notification','aml_declaration','suitability_declaration'),'granted':B},'customer_confidential')
evt('research.candidate_superseded','M05','M06,M07','research_candidate',{'candidate_id':U,'replacement_candidate_id':U,'previous_artifact_sha256':SHA,'replacement_artifact_sha256':SHA},'research_confidential','舊review標記stale，不可自動覆寫或沿用簽章。')
evt('kyc.refresh_required','M03','M02,M10','kyc_submission',{'customer_id':U,'submission_id':U,'refresh_case_id':U,'due_at':T},'customer_confidential')
evt('finance.payment_failed','M14','M10','payment',{'payment_id':U,'application_id':U,'customer_id':U,'retry_allowed':B,'customer_safe_reason_code':enum('payment_incomplete','payment_not_authorized','payment_provider_unavailable')},'customer_confidential')
evt('contract.service_ended','M02','M11,M10,M14','contract_service',{'contract_service_id':U,'contract_id':U,'customer_id':U,'ended_at':T},'customer_confidential','單一服務終止；M11需重新查全部付費服務，不能據此直接判定全部中斷。')
envelope={
 'event_id':U,'event_type':string(maxlen=160),'schema_version':{'type':'string','pattern':r'^\d+\.\d+\.\d+$'},'tenant_id':U,'producer':string(maxlen=80),'aggregate_type':string(maxlen=80),'aggregate_id':U,'aggregate_version':POS,'sequence_no':POS,'occurred_at':T,'emitted_at':T,'correlation_id':U,'causation_id':nullable(U),'idempotency_key':string(maxlen=160),'classification':enum('internal','customer_confidential','aml_restricted','research_confidential'),'payload_sha256':SHA}
# Schema-derived fixture helper: returns type-valid deterministic SYNTHETIC data, not business-valid approvals.
def sample(schema,key='value',depth=0):
 if depth>20: raise RuntimeError('schema recursion')
 if '$ref' in schema: return sample(S[schema['$ref'].split('/')[-1]],key,depth+1)
 if 'oneOf' in schema:return sample(schema['oneOf'][0],key,depth+1)
 if 'const' in schema:return schema['const']
 if 'enum' in schema:return schema['enum'][0]
 if 'anyOf' in schema:
  for branch in schema['anyOf']:
   if branch.get('type')=='null':return None
  return sample(schema['anyOf'][0],key,depth+1)
 typ=schema.get('type')
 if typ=='object':
  fields=schema.get('required',list(schema.get('properties',{})))
  if not fields and schema.get('minProperties',0): fields=list(schema['properties'])[:schema['minProperties']]
  data={k:sample(schema['properties'][k],k,depth+1) for k in fields}
  for condition in schema.get('allOf',[]):
   cond=condition.get('if',{}).get('properties',{})
   if all(data.get(k)==v.get('const') for k,v in cond.items()):
    for k in condition.get('then',{}).get('required',[]):data[k]=sample(schema['properties'][k],k,depth+1)
  return data
 if typ=='array':return [sample(schema['items'],key,depth+1) for _ in range(schema.get('minItems',0))]
 if typ=='boolean':return False
 if typ=='integer':return schema.get('minimum',1)
 if typ=='number':return schema.get('minimum',0)
 if typ=='null':return None
 if schema.get('format')=='uuid':return str(uuid.uuid5(uuid.NAMESPACE_URL,'longgood.synthetic.'+key))
 if schema.get('format')=='date-time':return '2026-12-22T00:00:00Z' if key in {'valid_until','expires_at','next_review_due_at','due_at','review_due_at','service_ends_at','service_period_end','first_contact_due_at','resolution_due_at','first_response_due_at'} else '2026-11-22T00:00:00Z'
 if schema.get('format')=='date':return '2026-11-22'
 if schema.get('format')=='uri':return 'https://example.invalid/synthetic/'+key
 if schema.get('format')=='email':return 'synthetic@example.invalid'
 pat=schema.get('pattern','')
 if pat=='^[a-f0-9]{64}$':return hashlib.sha256(('synthetic:'+key).encode()).hexdigest()
 if '0(?:' in pat and '1(?:' in pat:return '0.03'
 if pat=='^[A-Z]{3}$':return 'TWD'
 if '-?' in pat and '\\d{0,13}' in pat:return '100.000000'
 if pat=='^\\d{4}-\\d{2}$':return '2026-11'
 if pat.startswith('^"'):return '"1"'
 if schema.get('minLength',0)>len('synthetic'):return 'synthetic'.ljust(schema['minLength'],'x')
 return ('synthetic_'+key)[:schema.get('maxLength',500)]
import hashlib
registry=[]
for e in EV:
 fields={**envelope,'event_type':{'const':e['event_type'],'type':'string'},'schema_version':{'const':e['schema_version'],'type':'string'},'producer':{'const':e['producer'],'type':'string'},'aggregate_type':{'const':e['aggregate_type'],'type':'string'},'classification':{'const':e['classification'],'type':'string'},'payload':obj(e['payload_properties'])}
 fn=e['event_type']+'.v1.schema.json'
 sch={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'https://schemas.example.invalid/longgood/'+fn,'title':e['event_type'],**obj(fields),'description':e['purpose'] or 'LongGood內部已持久化事實；consumer不得直接相信事件即可越過自身授權與版本檢查。'}
 (ROOT/'events/schemas'/fn).write_text(json.dumps(sch,ensure_ascii=False,indent=2)+'\n')
 ex=sample(sch);ex['event_id']=str(uuid.uuid5(uuid.NAMESPACE_URL,e['event_type']))
 aggregate_keys={'kyc_submission':'submission_id','suitability_assessment':'assessment_id','aml_case':'case_id','business_clearance':'clearance_id','research_candidate':'candidate_id','maintenance_assignment':'assignment_id','commission_run':'run_id','commission_adjustment':'adjustment_id','service_case':'case_id','revenue_recognition':'recognition_id','accounting_export':'export_id','service_response':'response_id'}
 aggregate_key=aggregate_keys.get(e['aggregate_type'],e['aggregate_type']+'_id')
 if aggregate_key in ex['payload']:ex['aggregate_id']=ex['payload'][aggregate_key]
 ex['idempotency_key']=e['event_type']+':synthetic-1'
 ex['payload_sha256']=hashlib.sha256(json.dumps(ex['payload'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 (ROOT/'events/examples'/(e['event_type']+'.v1.json')).write_text(json.dumps(ex,ensure_ascii=False,indent=2)+'\n')
 registry.append({k:e[k] for k in ['event_type','schema_version','producer','consumers','aggregate_type','classification','purpose']}|{'schema':'schemas/'+fn,'example':'examples/'+e['event_type']+'.v1.json','ordering_key':'tenant_id:aggregate_type:aggregate_id','delivery_semantics':'at_least_once','dedup_key':'consumer:tenant_id:event_id'})
(ROOT/'events/registry.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n')
fixtures={r['operation_id']:{'module':r['module'],'request':sample(S[r['request']]) if r['request'] else None,'response_status':r['status'],'response':sample(S[r['response']])} for r in OPS}
(ROOT/'mocks/fixtures.json').write_text(json.dumps(fixtures,ensure_ascii=False,indent=2)+'\n')
print('events',len(registry),'fixtures',len(fixtures))
