#!/usr/bin/env python3
"""Generate the reviewed DDL and dictionary from an explicit, versioned catalog.

This generator is a design source, not a runtime migration tool. Regeneration must
be reviewed with the SQL diff. Existing deployed migrations must never be edited.
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
TABLES = []

def c(name, sql_type, description, required=True, default=None, classification="internal"):
    return dict(name=name, sql_type=sql_type, description=description,
                required=required, default=default, classification=classification)

def ref(name, target, description=None, required=True):
    x = c(name, "uuid", description or f"指向 {target}，必須同一 tenant。", required)
    x["references"] = target
    return x

def status(values, default):
    return c("status", "text", "狀態：" + " / ".join(values), default=f"'{default}'"), "CHECK (status IN (" + ",".join(repr(x) for x in values) + "))"

def table(name, module, purpose, retention, columns, checks=(), uniques=(), immutable=False):
    cols = [c("tenant_id", "uuid", "組織隔離鍵；由可信任服務在交易開始設定。"),
            c("id", "uuid", "資源識別碼；在同一租戶內唯一。", default="gen_random_uuid()"),
            c("version", "integer", "CAS 樂觀鎖版本；更新時由資料庫遞增。", default="1"),
            c("created_at", "timestamptz", "建立的 UTC 絕對時間。", default="clock_timestamp()"),
            c("updated_at", "timestamptz", "最近更新時間；歷史證據列不可更新。", default="clock_timestamp()")]
    cols += columns
    TABLES.append(dict(name=name,module=module,purpose=purpose,retention=retention,
                       columns=cols,checks=list(checks),uniques=list(uniques),immutable=immutable))

def st(values, default):
    x, check = status(values.split(), default)
    return x, check

def money(name, description, required=True):
    return c(name, "numeric(20,6)", description, required)

def j(name, description, required=True, default=None, classification="internal"):
    return c(name,"jsonb",description,required,default,classification)

def time(name, description, required=True):
    return c(name,"timestamptz",description,required)

def txt(name, description, required=True, classification="internal"):
    return c(name,"text",description,required,classification=classification)

def h(name, description, required=True):
    return c(name,"char(64)",description,required)

def S(name, module, purpose, retention, values, default, columns, checks=(), uniques=(), immutable=False):
    sc, ck = st(values, default)
    table(name,module,purpose,retention,[sc]+columns,[ck]+list(checks),uniques,immutable)

table("core.actor","SHARED","操作人或服務身分；不儲存認證秘密。","employment_access",[
    txt("subject_ref","SSO 供應商 subject 或服務主體；不可當成顯示姓名。"),
    txt("actor_type","human 或 service"), txt("display_label","最小必要顯示名稱",classification="personal"),
    c("enabled","boolean","是否可發起新操作",default="true"),
    j("role_codes","核准角色代碼；真正授權須經伺服器 RBAC/ABAC。",default="'[]'::jsonb"),
    time("departed_at","員工離職生效時點；觸發歸戶與權限撤銷。",False)
], ["CHECK (actor_type IN ('human','service'))"], [("subject_ref",)])
table("core.staff_credential","SHARED","分析師及招攬人員資格核驗版本。","employment_access",[
    ref("actor_id","core.actor"),txt("credential_type","analyst / sales / compliance / aml"),
    txt("verification_ref","受限資格查核證據引用"),time("valid_from","有效開始"),time("valid_until","有效截止",False),
    time("revoked_at","資格撤銷生效時間",False)
], ["CHECK (valid_until IS NULL OR valid_until > valid_from)"], immutable=True)
table("core.credential_revocation","SHARED","資格撤銷使用新事件，不更新不可變資格證據。","employment_access",[
    ref("credential_id","core.staff_credential"),ref("revoked_by","core.actor"),
    time("revoked_at","撤銷生效時間"),txt("reason_code","撤銷原因代碼")
],immutable=True)
table("core.retention_policy","SHARED","資料類型的保存政策版本；不把提案天數當成核准法定天數。","policy",[
    txt("policy_code","業務保存類型"),c("policy_version","integer","政策版本"),
    txt("trigger_event","起算事件，例如 contract_obligations_ended 或 report_provided"),
    c("duration_days","integer","核准的最低保存日數；null 代表尚未核定，禁止自動刪除。",False),
    txt("legal_basis_ref","法源或內規控制索引",False),ref("approved_by","core.actor",required=False),time("approved_at","核准時間",False)
], ["CHECK (duration_days IS NULL OR duration_days > 0)","CHECK ((approved_at IS NULL) = (approved_by IS NULL))"],[("policy_code","policy_version")],True)
table("core.object_asset","SHARED","加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。","per_asset",[
    txt("storage_key","私有 bucket/key；下載另用短效授權"),txt("object_version","物件儲存版本"),
    h("sha256","實際 bytes SHA-256；不等同另一個簽章容器的 hash。"),c("byte_length","bigint","長度"),
    txt("media_type","MIME 類型"),txt("classification","public / internal / personal / restricted_aml"),
    txt("key_ref","KMS key reference；不是密鑰"),ref("retention_policy_id","core.retention_policy"),
    time("retention_started_at","保存起算事件時間；未發生時 null。",False),time("delete_not_before","不得早於此時點刪除；null 禁止排程刪除。",False)
], ["CHECK (byte_length >= 0)","CHECK (classification IN ('public','internal','personal','restricted_aml'))"],[("storage_key","object_version")],True)
table("core.legal_hold","SHARED","司法、爭議或稽核保存指示；解除以新事件表達。","audit",[
    txt("target_type","資源類型"),c("target_id","uuid","被保全資源"),txt("action","hold 或 release"),
    txt("reason_code","保全理由代碼；詳細理由留在受控案件"),ref("actor_id","core.actor"),
    ref("prior_hold_id","core.legal_hold",required=False),time("effective_at","生效時間")
], ["CHECK (action IN ('hold','release'))","CHECK (action <> 'release' OR prior_hold_id IS NOT NULL)"], immutable=True)
table("core.audit_event","SHARED","不可覆寫之操作軌跡；完整性鏈由匯出封存驗證。","audit",[
    ref("actor_id","core.actor",required=False),txt("action","動作代碼"),txt("resource_type","資源類型"),c("resource_id","uuid","資源 ID"),
    c("correlation_id","uuid","全鏈路追蹤 ID"),txt("outcome","success / denied / error"),txt("classification","internal/personal/financial/restricted_aml"),
    j("redacted_changes","僅欄位名稱與遮罩值；禁止原始 KYC/AML/秘密。",default="'{}'::jsonb"),
    time("occurred_at","實際發生時間"),h("prior_hash","同一封存鏈前一證據 hash",False),h("event_hash","canonical audit bytes 的摘要")
], ["CHECK (outcome IN ('success','denied','error'))","CHECK (classification IN ('internal','personal','financial','restricted_aml'))","CHECK (resource_type NOT LIKE 'aml.%' OR classification='restricted_aml')"],immutable=True)
S("core.outbox","SHARED","與業務異動同一 DB transaction 提交的待送事件。","integration","pending leased published retry dead_letter","pending",[
    c("event_id","uuid","穩定事件 ID"),txt("event_type","事件名稱；與 events/registry.json 一致"),c("schema_version","text","事件 schema SemVer",default="'1.0.0'"),
    txt("producer","權威 producer 模組代碼"),txt("aggregate_type","aggregate 類型"),c("aggregate_id","uuid","aggregate ID"),c("aggregate_version","integer","aggregate 資料版本"),
    c("sequence_no","bigint","同 producer/aggregate 事件順序"),txt("idempotency_key","穩定命令／事件去重 key"),txt("classification","事件資料分類"),h("payload_sha256","canonical payload SHA-256"),
    j("payload","只含 IDs、版本、狀態與 safe amount；禁止敏感原文。"),c("correlation_id","uuid","trace"),c("causation_id","uuid","上游事件或命令 ID",False),
    time("occurred_at","業務實際時間"),time("emitted_at","首次建立外送 envelope 的真實時間；重試不變"),time("available_at","允許送出時間",False),time("lease_until","worker 租約",False),
    txt("lease_owner","worker ID",False),c("attempt_count","integer","嘗試次數",default="0"),txt("last_error_code","非敏感錯誤代碼",False)
], ["CHECK (attempt_count >= 0)","CHECK (aggregate_version > 0 AND sequence_no > 0)"],[("event_id",),("producer","aggregate_type","aggregate_id","sequence_no"),("producer","idempotency_key")])
S("core.inbox","SHARED","consumer 去重與 event fingerprint；同 ID 不同 payload 必須拒收。","integration","received leased applied rejected retry dead_letter","received",[
    txt("consumer","consumer 名稱"),txt("producer","來源服務"),c("event_id","uuid","來源事件 ID"),h("payload_sha256","raw 或約定 canonical payload hash"),
    txt("event_type","來源事件名稱"),txt("schema_version","來源 schema SemVer"),txt("aggregate_type","來源 aggregate 類型"),c("aggregate_id","uuid","來源 aggregate ID"),c("aggregate_version","integer","來源版本"),c("sequence_no","bigint","來源 aggregate 事件順序"),time("received_at","接收時間"),time("lease_until","處理租約",False),
    time("applied_at","交易內成功套用時間",False),txt("result_code","結果代碼",False)
],uniques=[("consumer","producer","event_id")])
table("core.idempotency_record","SHARED","命令重送收據；必須綁 tenant、actor、operation、key 與 request hash。","integration",[
    ref("actor_id","core.actor"),txt("operation","API operationId"),txt("idempotency_key","呼叫端提供 key；不可含 PII"),h("request_sha256","請求指紋"),
    c("response_status","integer","已確定的 HTTP 狀態",False),j("redacted_response","僅無秘密的重播回應或 resource refs",False),
    time("completed_at","完成時間",False),time("expires_at","收據保存截止；不得短於安全重試窗")
],[("CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599)")],[("actor_id","operation","idempotency_key")])
table("core.job_lease","SHARED","排程任務租約與 fencing token。","integration",[
    txt("job_key","tenant 範圍內排程 key"),txt("worker_id","持租 worker"),c("fence_token","bigint","每次接管遞增 token"),
    time("lease_until","租約到期"),time("checkpoint_at","最後成功 checkpoint",False),j("checkpoint","安全的游標",default="'{}'::jsonb")
],uniques=[("job_key",)])

S("customer.customer","M01","客戶管理主檔；潛在、申辦、已簽與已生效客戶共用身份。","customer","prospect applicant active inactive restricted merged","prospect",[
    c("pii_ciphertext","bytea","最小客戶識別資料；application-layer envelope encryption。",False,classification="personal"),
    txt("pii_key_ref","解密所需 KMS reference",False),h("lookup_hmac","身分正規化後 keyed HMAC；不得用可逆 raw id 或無鹽 hash。",False),
    ref("merged_into_id","customer.customer",required=False),time("first_effective_paid_at","首次付費服務生效日期；與首次造訪分離。",False),
    txt("locale","客戶語系",False)
], ["CHECK ((pii_ciphertext IS NULL) = (pii_key_ref IS NULL))","CHECK (merged_into_id IS NULL OR merged_into_id <> id)"],[("lookup_hmac",)])
table("customer.external_identity","M01","經驗證外部登入身分；合併須證據與審核。","customer",[
    ref("customer_id","customer.customer"),txt("provider","identity provider，例如 legendflow/line/oidc"),h("subject_hmac","provider subject 的 keyed HMAC"),
    time("verified_at","完成身份連結驗證時間"),txt("assurance_level","已核准 assurance code"),ref("proof_asset_id","core.object_asset",required=False)
],uniques=[("provider","subject_hmac")])
S("customer.application","M01","簽約申辦流程；客戶簽署先於 KYC，不代表公司已接受。","contract","draft customer_signed due_diligence_pending review_in_progress accepted rejected withdrawn","draft",[
    ref("customer_id","customer.customer"),txt("service_code","受申請服務，商品價格版本由 contract 固定"),
    ref("package_version_id","contract.package_version"),j("service_selections","所選服務、報價與稅類版本的服務端快照；可含多服務。"),
    c("intent","text","new/renewal/upgrade/reactivation",default="'new'"),ref("preceding_contract_id","contract.contract",required=False),
    txt("source_session_ref","去識別漏斗 session 參考",False),time("submitted_at","送審時間",False),time("decision_at","公司決定時間",False),
    txt("decision_reason_code","公司決定代碼；客戶可見與受限理由分開。",False)
],["CHECK (intent IN ('new','renewal','upgrade','reactivation'))","CHECK (jsonb_typeof(service_selections)='array' AND jsonb_array_length(service_selections)>0)"])
table("customer.consent","M01","個資告知、行銷同意、撤回事件；不能用簽約強迫行銷同意。","customer",[
    ref("customer_id","customer.customer"),txt("purpose","同意目的"),txt("notice_version","所見告知版本"),c("granted","boolean","true 同意／false 撤回"),
    time("recorded_at","客戶作出意思表示時間"),ref("evidence_asset_id","core.object_asset"),txt("channel","取得來源")
],immutable=True)
S("customer.merge_case","M01","重複客戶合併申請，核准後保留來源 ID alias 與原證據。","customer","proposed approved rejected executed cancelled","proposed",[
    ref("source_customer_id","customer.customer"),ref("target_customer_id","customer.customer"),
    ref("proposed_by","core.actor"),ref("reviewed_by","core.actor",required=False),ref("evidence_asset_id","core.object_asset"),
    time("decided_at","覆核時間",False),time("executed_at","完成交易時間",False),txt("reason_code","合併理由代碼")
],["CHECK (source_customer_id <> target_customer_id)","CHECK (reviewed_by IS NULL OR reviewed_by <> proposed_by)","CHECK (status NOT IN ('approved','executed') OR reviewed_by IS NOT NULL)"])
table("contract.package_version","M02","申請暨投顧契約及揭露文件完整版本。","contract",[
    txt("package_code","模板識別碼"),c("package_version","integer","模板版本"),ref("asset_id","core.object_asset"),h("package_sha256","完整展示文件 hash"),
    time("effective_from","模板啟用時間"),ref("proposed_by","core.actor"),ref("approved_by","core.actor",required=False),time("approved_at","模板核准時間",False),j("acceptance_conditions","公司接受與生效條件；須核定正式文字。"),
    txt("approval_status","draft/approved；draft 不得供正式簽署")
], ["CHECK (approval_status IN ('draft','approved','retired'))","CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by))"],[("package_code","package_version")])
table("contract.customer_signature","M02","客戶先簽申請／契約封包的不可變證據。","contract",[
    ref("application_id","customer.application"),ref("package_version_id","contract.package_version"),ref("customer_id","customer.customer"),
    h("content_sha256","簽署前精確內容摘要"),h("signed_file_sha256","簽後容器或檔案摘要；不同於 content hash"),
    ref("signed_asset_id","core.object_asset"),txt("provider","簽章 provider"),txt("provider_transaction_id","供應商交易 ID"),
    txt("assurance_level","身份驗證方式的核准代碼"),time("customer_signed_at","客戶真正簽署時點"),ref("evidence_asset_id","core.object_asset")
],uniques=[("provider","provider_transaction_id")],immutable=True)
table("contract.acceptance_decision","M02","公司接受、拒絕或要求補件的完整決策歷史；accept才另產生company_acceptance。","contract",[
    ref("application_id","customer.application"),ref("customer_signature_id","contract.customer_signature"),
    txt("decision","accept/reject/request_changes"),ref("decided_by","core.actor"),time("decided_at","公司決定真實時間"),
    txt("safe_reason_code","可供申辦工作流使用的理由代碼；不含AML調查內情"),j("evidence_snapshot","決策依據的版本引用；受限內容留在M04。")
],["CHECK (decision IN ('accept','reject','request_changes'))"],immutable=True)
table("contract.company_acceptance","M02","公司完成必要審查後接受；與客戶簽署分離。","contract",[
    ref("decision_id","contract.acceptance_decision"),
    ref("application_id","customer.application"),ref("customer_signature_id","contract.customer_signature"),
    ref("kyc_submission_id","kyc.submission"),ref("suitability_assessment_id","kyc.suitability_assessment"),ref("aml_clearance_id","aml.business_clearance"),
    ref("accepted_by","core.actor"),time("accepted_at","公司接受真實時間"),ref("company_signed_asset_id","core.object_asset",required=False),
    j("conditions_snapshot","本次公司接受檢查條件與版本；不能改寫原評估。")
],uniques=[("application_id",),("decision_id",)],immutable=True)
S("contract.contract","M02","法律關係及服務契約狀態；不把付款或前端權限當成成立證據。","contract","pending_company_acceptance accepted effective suspended expired terminated","pending_company_acceptance",[
    ref("application_id","customer.application"),ref("customer_id","customer.customer"),ref("customer_signature_id","contract.customer_signature"),
    ref("company_acceptance_id","contract.company_acceptance",required=False),txt("contract_number","不可重複契約編號"),
    time("effective_at","依核准條款生效時間",False),time("ends_at","約定服務終止時間",False),time("obligations_ended_at","權利義務全部終了；保存起算依政策。",False),
    txt("currency","ISO currency，例 TWD"),j("terms_snapshot","價格、稅類、服務、退款條款與版本快照")
], ["CHECK (ends_at IS NULL OR effective_at IS NULL OR ends_at > effective_at)","CHECK (status NOT IN ('accepted','effective') OR company_acceptance_id IS NOT NULL)"],[("contract_number",),("application_id",)])
table("contract.contract_service","M02","一客多契約多服務及續約服務鏈。","contract",[
    ref("contract_id","contract.contract"),txt("service_code","服務權益代碼"),c("service_chain_id","uuid","續訂關聯鏈；不因改方案或換業務歸零。"),
    ref("predecessor_service_id","contract.contract_service",required=False),time("starts_at","服務區間 [start,end) 開始"),time("ends_at","服務區間結束"),
    c("auto_renew","boolean","本期付款前客戶授權之自動續訂",default="false"),money("untaxed_contract_amount","已確認稅別後未稅契約額；未履約部分不直接當收入。"),
    txt("tax_profile_ref","財會核准稅務設定版本"),j("features_snapshot","核准的服務權益快照")
],["CHECK (ends_at > starts_at)","CHECK (untaxed_contract_amount >= 0)"])
S("contract.entitlement","M02","權限來源為有效契約與審查結果；配送時重新檢查。","contract","locked eligible active suspended expired revoked","locked",[
    ref("customer_id","customer.customer"),ref("contract_service_id","contract.contract_service"),txt("feature_code","可用權益"),
    time("valid_from","使用權限開始"),time("valid_until","使用權限結束"),txt("payment_condition_ref","付款或核准授信條件證據",False),
    txt("restriction_code","安全可見限制代碼；不放 AML 內情",False)
],["CHECK (valid_until > valid_from)","CHECK (status <> 'active' OR payment_condition_ref IS NOT NULL)"],[("contract_service_id","feature_code")])
table("contract.entitlement_decision","M02","每次啟停權限的決策紀錄。","audit",[
    ref("entitlement_id","contract.entitlement"),txt("from_status","舊狀態"),txt("to_status","新狀態"),
    ref("actor_id","core.actor"),txt("reason_code","規則決策代碼"),j("gate_snapshot","公司接受、合約、KYC、適合度、AML safe clearance、付款條件版本"),time("effective_at","生效時間")
],immutable=True)
S("kyc.submission","M03","版本化線上 KYC 回答與證據；先保留客戶原始填答。","kyc","not_started in_progress submitted incomplete under_review approved rejected refresh_required","not_started",[
    ref("application_id","customer.application"),ref("customer_id","customer.customer"),c("submission_version","integer","同一申請送件版本"),
    c("answers_ciphertext","bytea","KYC 答案加密；不得送 general LLM",False,classification="personal"),txt("key_ref","KMS ref",False),
    time("submitted_at","送件時間",False),ref("reviewed_by","core.actor",required=False),time("reviewed_at","審查時間",False),time("valid_until","覆核有效截止",False),
    txt("decision_reason_code","審查代碼",False)
], ["CHECK ((answers_ciphertext IS NULL) = (key_ref IS NULL))","CHECK (status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))"],[("application_id","submission_version")])
table("kyc.submission_document","M03","線上補件、驗證用途與結果；保留原件版本。","kyc",[
    ref("submission_id","kyc.submission"),ref("asset_id","core.object_asset"),txt("document_type","核准文件種類"),
    txt("verification_state","pending/verified/rejected"),ref("verified_by","core.actor",required=False),time("verified_at","驗證時間",False)
],["CHECK (verification_state IN ('pending','verified','rejected'))"])
S("kyc.suitability_assessment","M03","版本化適合度評估，條件適合必須附限制。","kyc","pending suitable conditional not_suitable reassessment_due","pending",[
    ref("submission_id","kyc.submission"),ref("customer_id","customer.customer"),txt("questionnaire_version","問卷版本"),txt("rule_version","計分及判斷規則版本"),
    j("answers_snapshot","需加密的原始答案引用與問題版本，不存明文個資。"),j("restrictions","條件適合的限制代碼；供 entitlement 使用。",default="'[]'::jsonb"),
    ref("assessed_by","core.actor",required=False),time("assessed_at","最終評估時點",False),time("valid_until","重評期限",False)
],["CHECK (status <> 'conditional' OR jsonb_array_length(restrictions) > 0)"])
S("aml.screening","M04","名單比對供應商查核；模糊命中不是自動認定或自動清除。","aml","pending clear possible_match confirmed_match error","pending",[
    ref("customer_id","customer.customer"),ref("submission_id","kyc.submission"),txt("provider","名單 provider"),txt("provider_request_id","查核唯一請求"),
    txt("list_version","名單版本"),h("request_sha256","查核輸入 fingerprint"),ref("restricted_result_asset_id","core.object_asset",required=False),time("screened_at","查核時間",False)
],uniques=[("provider","provider_request_id")])
S("aml.case_file","M04","AML/CFT 受限覆核案件，禁止 CRM 讀內情。","aml","open under_review escalated cleared rejected closed","open",[
    ref("customer_id","customer.customer"),ref("screening_id","aml.screening"),ref("owner_actor_id","core.actor",required=False),
    txt("risk_level","low/medium/high/undetermined"),c("restricted_notes_ciphertext","bytea","案件註記加密",False,classification="restricted_aml"),
    txt("key_ref","KMS ref",False),time("next_review_at","下一個必要覆核時間",False)
],["CHECK (risk_level IN ('low','medium','high','undetermined'))"])
table("aml.case_action","M04","AML 調查、EDD、申報與決定之受限軌跡。","aml",[
    ref("case_id","aml.case_file"),ref("actor_id","core.actor"),txt("action_code","受限動作代碼"),
    ref("evidence_asset_id","core.object_asset",required=False),time("occurred_at","動作時間"),c("restricted_detail_ciphertext","bytea","受限詳情",False,classification="restricted_aml")
],immutable=True)
table("aml.business_clearance","M04","供公司接受服務查核的最小結果；不暴露比對名單、申報或案件細節。","aml",[
    ref("customer_id","customer.customer"),ref("application_id","customer.application"),ref("case_id","aml.case_file",required=False),
    txt("decision","approved / pending / denied / expired"),txt("policy_version","核定依據政策，M02不需讀受限screening"),ref("decided_by","core.actor"),time("decided_at","核定時間"),
    time("valid_until","有效期限"),txt("safe_reason_code","僅 generic review_required / approved 等安全代碼")
],["CHECK (decision IN ('approved','pending','denied','expired'))","CHECK (valid_until > decided_at)"],immutable=True)

S("research.candidate","M05","Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。","research","received validating quarantined ready rejected superseded","received",[
    txt("producer","外部 producer"),txt("external_candidate_id","來源候選識別碼"),c("external_version","integer","來源版本"),
    txt("origin","persona_autonomous / analyst_requested"),txt("persona_ref","Persona 引用",False),txt("pool_ref","研究／交易 pool 引用",False),
    txt("artifact_ref","Pantheon artifact 指標"),h("content_sha256","實際候選內容 hash"),ref("snapshot_asset_id","core.object_asset"),
    time("source_occurred_at","來源真實發生時間"),time("received_at","本平台接收時間"),j("lineage","ticket/plan/run/model/prompt/data/evidence 版本引用")
],["CHECK (origin IN ('persona_autonomous','analyst_requested'))"],[("producer","external_candidate_id","external_version")])
table("research.candidate_evidence","M05","候選報告採用來源快照、時間與授權範圍。","research",[
    ref("candidate_id","research.candidate"),txt("evidence_ref","Pantheon evidence/bundle reference"),h("evidence_sha256","可驗證內容摘要"),
    time("event_time","來源事件時間"),time("available_time","研究當時可取得時間"),txt("license_scope","授權或傳閱限制"),ref("snapshot_asset_id","core.object_asset",required=False)
],uniques=[("candidate_id","evidence_ref")],immutable=True)
S("review.case_file","M06","分析師覆核工作項；非交易治理批准。","research","queued assigned reviewing changes_requested ready_to_sign rejected closed","queued",[
    ref("candidate_id","research.candidate"),ref("analyst_id","core.actor",required=False),c("current_revision_no","integer","目前採用修訂",default="1"),
    txt("priority","normal/urgent"),time("due_at","工作期限",False),c("stale","boolean","候選新版本到達或證據變更",default="false")
],["CHECK (priority IN ('normal','urgent'))"],[("candidate_id",)])
table("review.conversation","M06","Agora Servant 對話參照；不複製所有 Pantheon 知識。","research",[
    ref("case_id","review.case_file"),txt("provider","agora"),txt("external_conversation_id","外部 conversation"),txt("scoped_context_ref","最小上下文引用"),
    ref("created_by","core.actor"),time("last_synced_at","最後同步時間",False)
],uniques=[("provider","external_conversation_id")])
S("review.revision_request","M06","分析師提出修訂並綁精確輸入版本。","research","requested accepted running completed failed cancelled stale","requested",[
    ref("case_id","review.case_file"),ref("conversation_id","review.conversation"),ref("requested_by","core.actor"),
    c("base_revision_no","integer","被修改版本"),ref("instruction_asset_id","core.object_asset"),txt("external_job_ref","Servant job",False),time("requested_at","請求時間")
])
table("review.revision_result","M06","修訂產物不可變，新稿不覆寫已審稿。","research",[
    ref("request_id","review.revision_request"),c("revision_no","integer","新修訂編號"),txt("artifact_ref","外部產物"),h("content_sha256","新產物內容摘要"),
    ref("asset_id","core.object_asset"),time("produced_at","產生時間"),j("evidence_refs","本次引用來源；可對回 research evidence")
],uniques=[("request_id","revision_no")],immutable=True)
table("report.report","M07","正式投顧研究報告的穩定識別。","report",[
    ref("review_case_id","review.case_file"),txt("report_number","正式報告編號"),txt("title","標題，不含客戶個資"),
    j("instrument_refs","研究標的 ID／市場；並不等同可交易指令。"),txt("report_type","initial/update/exit/supplement")
],["CHECK (report_type IN ('initial','update','exit','supplement'))"],[("report_number",)])
S("report.report_version","M07","不可變報告正文版本及簽前核定 bundle；狀態投影可前進。","report","draft in_review changes_requested ready_to_sign signature_pending signed superseded withdrawn","draft",[
    ref("report_id","report.report"),c("report_version_no","integer","報告版號"),ref("candidate_id","research.candidate"),
    ref("revision_result_id","review.revision_result",required=False),ref("content_asset_id","core.object_asset"),h("content_sha256","核准報告原 bytes hash"),
    h("bundle_sha256","包含 channel 文案與 audience 規則版本的 canonical bundle hash"),ref("bundle_manifest_asset_id","core.object_asset"),j("bundle_manifest","確定的訊息版本、正文、受眾規則、風險揭露引用"),
    txt("review_policy_version","需要哪些 professional/compliance review 的政策版本"),c("requires_compliance_review","boolean","是否須法遵先行覆核",default="true"),
    time("valid_from","研究建議開始有效時間"),time("valid_until","到期不可再發送"),time("frozen_at","簽前凍結時間",False)
],["CHECK (valid_until > valid_from)","CHECK (status NOT IN ('ready_to_sign','signature_pending','signed') OR frozen_at IS NOT NULL)"],[("report_id","report_version_no")])
table("report.review_decision","M07","逐版本覆核，綁相同內容與 bundle；修訂後不能沿用。","report",[
    ref("report_version_id","report.report_version"),txt("review_type","professional/compliance"),txt("decision","approved/changes_requested/rejected"),
    ref("reviewer_id","core.actor"),h("content_sha256","被審查正文 hash"),h("bundle_sha256","被審查 bundle hash"),time("reviewed_at","真實覆核時間"),
    ref("evidence_asset_id","core.object_asset",required=False)
],["CHECK (review_type IN ('professional','compliance'))","CHECK (decision IN ('approved','changes_requested','rejected'))"],immutable=True)
S("report.signature_request","M07","簽章 provider 呼叫狀態；逾時需查詢，不可盲目重簽。","report","pending submitted verified failed expired","pending",[
    ref("report_version_id","report.report_version"),ref("signer_id","core.actor"),ref("credential_id","core.staff_credential"),h("bundle_sha256","待簽 bundle hash"),
    txt("provider","簽章供應商"),txt("provider_transaction_id","provider transaction",False),time("requested_at","要求時間"),time("expires_at","簽署挑戰到期")
],uniques=[("provider","provider_transaction_id")])
table("report.signature","M07","已驗證分析師簽章證據；永不改內容。撤銷用 revocation。","report",[
    ref("request_id","report.signature_request"),ref("report_version_id","report.report_version"),ref("signer_id","core.actor"),ref("credential_id","core.staff_credential"),
    h("content_sha256","被簽報告正文 hash"),h("bundle_sha256","被簽完整服務訊息 bundle hash"),h("signed_file_sha256","簽後 PDF/容器 hash"),
    ref("signed_asset_id","core.object_asset"),ref("verification_asset_id","core.object_asset"),time("signed_at","實際簽章時間"),time("verified_at","驗簽成功時間")
],["CHECK (verified_at >= signed_at)"],[("request_id",),("report_version_id",)],True)
table("report.revocation","M07","撤銷或重驗失敗保留原簽章證據。","report",[
    ref("signature_id","report.signature"),ref("actor_id","core.actor"),txt("reason_code","撤銷理由"),time("revoked_at","真實撤銷時間"),
    ref("evidence_asset_id","core.object_asset",required=False)
],immutable=True)
table("publication.recommendation_series","M08","帶進、追蹤、帶出的建議序列，與 Persona 交易及客戶交易分離。","report",[
    txt("series_code","建議序列碼"),j("instrument_refs","標的引用"),txt("strategy_ref","可選外部策略參考",False)
],uniques=[("series_code",)])
table("publication.recommendation_version","M08","正式訊息與同時有效報告連結；重大判斷改變需新簽證。","report",[
    ref("series_id","publication.recommendation_series"),c("recommendation_version_no","integer","序列版號"),txt("action_type","entry/update/exit/correction"),
    ref("report_version_id","report.report_version"),j("channel_content","已覆核 channel 文案或物件引用；簽章後禁止 LLM 改寫投資判斷。"),
    h("content_sha256","channel canonical content hash"),txt("audience_rule_version","固定受眾規則版本"),time("valid_from","開始可用"),time("valid_until","到期不再發送"),
    ref("corrects_version_id","publication.recommendation_version",required=False)
],["CHECK (action_type IN ('entry','update','exit','correction'))","CHECK (valid_until > valid_from)"],[("series_id","recommendation_version_no")],True)
S("publication.publication","M08","驗簽完成即建立發布，不加第二個 Publish 人工作業。","report","queued publishing published partially_failed expired corrected withdrawn","queued",[
    ref("recommendation_version_id","publication.recommendation_version"),ref("signature_id","report.signature"),
    h("bundle_sha256","必須等於簽章 bundle"),time("queued_at","自動排入發布時間"),time("published_at","平台 feed 成功發布時間",False),
    time("expires_at","期限；不得超過報告與訊息有效期"),ref("correction_of_id","publication.publication",required=False),txt("withdrawal_reason_code","撤回理由",False)
],uniques=[("recommendation_version_id",)])
table("publication.audience_snapshot","M08","發布當下受眾規則及快照；不取代送出時再次查權限。","report",[
    ref("publication_id","publication.publication"),txt("rule_version","受眾規則版本"),time("evaluated_at","評估時間"),h("snapshot_sha256","收件人與權益版本快照 hash"),
    c("recipient_count","integer","人數"),ref("snapshot_asset_id","core.object_asset",required=False)
],["CHECK (recipient_count >= 0)"],[("publication_id",)],True)
table("publication.recipient","M08","逐收件人權益證據；不存姓名或手機。","report",[
    ref("snapshot_id","publication.audience_snapshot"),ref("customer_id","customer.customer"),ref("entitlement_id","contract.entitlement"),
    c("entitlement_version","integer","發布當下權益版本"),txt("eligibility_reason","安全的符合理由")
],uniques=[("snapshot_id","customer_id","entitlement_id")],immutable=True)
S("delivery.message","M09","每 recipient/channel 一個邏輯消息；供應商重試另建 attempt。","report","queued accepted sent delivered failed expired suppressed unknown","queued",[
    ref("publication_id","publication.publication"),ref("recipient_id","publication.recipient"),txt("channel","legendflow/line/sms/email"),
    ref("channel_binding_id","delivery.channel_binding",required=False),
    h("rendered_sha256","已核准文字 bytes hash"),ref("rendered_asset_id","core.object_asset"),txt("provider_idempotency_key","同一邏輯消息穩定 key"),
    time("expires_at","建議有效期限"),time("last_authorized_at","送出前最後權限查核時間",False),c("checked_entitlement_version","integer","送出前權益版本",False),
    time("read_at","僅 provider 可證實閱讀才填；沒有回執時 null。",False),txt("suppression_reason","退訂/到期/撤回等安全原因",False)
],["CHECK (channel IN ('legendflow','line','sms','email'))","CHECK (channel='legendflow' OR channel_binding_id IS NOT NULL)"],[("publication_id","recipient_id","channel"),("provider_idempotency_key",)])
S("delivery.channel_binding","M09","經驗證會員/LINE/手機/郵件收件身分；路由不可使用模型產生的新地址。","customer","verified unsubscribed blocked revoked","verified",[
    ref("customer_id","customer.customer"),txt("channel","web/line/sms/email"),txt("provider","通路provider"),
    c("destination_ciphertext","bytea","實際收件address/subject加密",classification="personal"),txt("key_ref","KMS reference"),h("destination_hmac","正規化收件subject的keyed HMAC"),
    ref("proof_asset_id","core.object_asset"),ref("external_identity_id","customer.external_identity",required=False),
    time("verified_at","身份與channel同一性核驗時間"),c("service_delivery_enabled","boolean","客戶服務通知通路授權",default="true")
],["CHECK (channel IN ('web','line','sms','email'))"],[("provider","channel","destination_hmac")])
table("delivery.attempt","M09","各次對 provider 實際傳送嘗試。","report",[
    ref("message_id","delivery.message"),c("attempt_no","integer","遞增嘗試編號"),txt("provider","供應商"),txt("provider_message_id","provider ID",False),
    txt("outcome","accepted/rejected/timeout/unknown"),time("started_at","開始呼叫"),time("finished_at","呼叫結束",False),txt("error_code","安全錯誤碼",False)
],["CHECK (outcome IN ('accepted','rejected','timeout','unknown'))"],[("message_id","attempt_no")],True)
table("delivery.receipt","M09","回呼事件不可變，亂序回呼不可把 delivered 降級為 sent。","report",[
    ref("message_id","delivery.message"),txt("provider","供應商"),txt("provider_event_id","回呼唯一 ID"),txt("receipt_type","accepted/sent/delivered/read/failed"),
    time("provider_occurred_at","provider 事件時間"),time("received_at","平台收到時間"),h("payload_sha256","驗證後回呼摘要")
],["CHECK (receipt_type IN ('accepted','sent','delivered','read','failed'))"],[("provider","provider_event_id")],True)

table("crm.marketing_attribution","M10","客戶最初來源及後續可稽核 attribution；不因換業務清除。","crm",[
    ref("customer_id","customer.customer"),txt("source_type","web/campaign/referral/manual/unknown"),txt("campaign_ref","campaign reference",False),
    txt("channel_ref","流量來源",False),time("acquired_at","取得潛客時間"),h("evidence_sha256","source evidence hash"),ref("evidence_asset_id","core.object_asset",required=False),
    ref("supersedes_id","crm.marketing_attribution",required=False)
],immutable=True)
S("crm.opportunity","M10","首次申辦、續訂、升級或重新開發機會。","crm","open qualifying proposed awaiting_customer converted lost cancelled","open",[
    ref("customer_id","customer.customer"),txt("kind","new/renewal/upgrade/reactivation"),ref("owner_actor_id","core.actor",required=False),
    ref("contract_service_id","contract.contract_service",required=False),txt("target_service_code","建議服務"),time("due_at","預定跟進時間",False),
    txt("loss_reason_code","流失原因",False)
],["CHECK (kind IN ('new','renewal','upgrade','reactivation'))"])
table("crm.activity","M10","真實業務行為及客戶回應證據；AI 建議、群發或分配名字不能單獨計獎。","crm",[
    ref("customer_id","customer.customer"),ref("opportunity_id","crm.opportunity",required=False),ref("actor_id","core.actor"),
    txt("activity_type","call/meeting/conversation/customer_request/automated_campaign"),time("occurred_at","實際接觸時間"),
    c("two_way","boolean","有客戶回應"),ref("evidence_asset_id","core.object_asset",required=False),txt("outcome_code","結果代碼"),
    c("qualifies_for_claim","boolean","僅候選證據，最終仍須 conversion 核定",default="false")
],immutable=True)
table("crm.conversion","M10","單筆交易銷售歸因；不是單純維護人欄位。","commission",[
    ref("customer_id","customer.customer"),ref("opportunity_id","crm.opportunity"),ref("contract_service_id","contract.contract_service"),
    ref("sales_actor_id","core.actor",required=False),ref("qualifying_activity_id","crm.activity",required=False),
    txt("conversion_type","self_service/sales_assisted"),txt("decision_status","proposed/approved/rejected/disputed"),
    ref("decided_by","core.actor",required=False),time("converted_at","完成續訂/升級時間"),ref("attribution_id","crm.marketing_attribution",required=False),
    txt("policy_version","歸因規則版本")
],["CHECK (conversion_type IN ('self_service','sales_assisted'))","CHECK (decision_status IN ('proposed','approved','rejected','disputed'))","CHECK (conversion_type <> 'sales_assisted' OR (sales_actor_id IS NOT NULL AND qualifying_activity_id IS NOT NULL))"],[("contract_service_id",)])
S("crm.work_item","M10","業務要執行的具體名單工作；不只顯示分數。","crm","open assigned in_progress done snoozed cancelled","open",[
    ref("customer_id","customer.customer"),ref("opportunity_id","crm.opportunity",required=False),ref("owner_actor_id","core.actor",required=False),
    txt("reason_code","incomplete_application/renewal/upgrade/recovery/service_risk 等"),txt("next_action","具體下一步"),time("due_at","到期時間"),
    txt("dedupe_key","避免同一客戶同類工作重複"),j("explanation","規則來源與可解釋證據 IDs；非 AML 明細。")
],uniques=[("dedupe_key",)])
table("crm.suppression","M10","限制招攬但不停止必要服務通知。","crm",[
    ref("customer_id","customer.customer"),txt("scope","marketing/all_sales/channel_code"),txt("reason_code","opt_out/complaint/hold/temporary"),
    time("valid_from","開始"),time("valid_until","截止，null 為直到另行撤銷",False),ref("actor_id","core.actor")
],["CHECK (valid_until IS NULL OR valid_until > valid_from)"])
table("ownership.policy_version","M11","Pool 判定政策；七天未聯絡不是既定規則。","policy",[
    txt("policy_code","pool policy"),c("policy_version","integer","版本"),txt("approval_status","draft/approved/retired"),ref("proposed_by","core.actor"),
    txt("lapse_basis","proposed_all_paid_services_ended；需依決策登錄核定"),c("lapse_days","integer","連續中斷天數",default="7"),
    c("departure_immediate","boolean","離職即歸公司",default="true"),ref("approved_by","core.actor",required=False),time("approved_at","核准時間",False)
],["CHECK (approval_status IN ('draft','approved','retired'))","CHECK (lapse_days > 0)","CHECK (approval_status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by))"],[("policy_code","policy_version")])
table("ownership.assignment","M11","維護關係的有效時間；不直接決定所有交易佣金。","commission",[
    ref("customer_id","customer.customer"),ref("owner_actor_id","core.actor",required=False),txt("owner_type","company_pool/sales"),
    time("valid_from","歸屬生效"),time("valid_until","歸屬終止，null 仍有效",False),txt("reason_code","assignment/departure/service_lapse/redevelopment"),
    ref("policy_version_id","ownership.policy_version"),ref("assigned_by","core.actor"),ref("predecessor_id","ownership.assignment",required=False)
],["CHECK (owner_type IN ('company_pool','sales'))","CHECK ((owner_type = 'sales') = (owner_actor_id IS NOT NULL))","CHECK (valid_until IS NULL OR valid_until > valid_from)"])
table("ownership.pool_entry","M11","客戶回公司 Pool 的證據與重新開發狀態。","commission",[
    ref("customer_id","customer.customer"),ref("assignment_id","ownership.assignment"),txt("reason_code","staff_departure/all_paid_services_lapsed"),
    time("all_paid_services_ended_at","全部付費服務中斷起點；離職時可 null",False),time("entered_at","真正歸公司時間"),
    ref("claimed_by","core.actor",required=False),time("claimed_at","新業務接手時間",False),ref("claim_activity_id","crm.activity",required=False),
    j("evaluation_snapshot","所查全部 service ids/status/period/policy；有年度 active 服務不可用月訂中斷判全客流失。")
],["CHECK (reason_code IN ('staff_departure','all_paid_services_lapsed'))","CHECK (reason_code <> 'all_paid_services_lapsed' OR all_paid_services_ended_at IS NOT NULL)"])

S("support.case_file","M13","線上線下統一客服案件；客服 owner 不等於業務歸屬。","support","new triaged assigned waiting_customer waiting_internal resolved closed reopened","new",[
    ref("customer_id","customer.customer",required=False),txt("case_number","案件號"),txt("category","service/complaint/refund/research/access/security"),
    txt("priority","low/normal/high/critical"),ref("owner_actor_id","core.actor",required=False),ref("contract_id","contract.contract",required=False),
    ref("publication_id","publication.publication",required=False),txt("sla_policy_version","SLA 規則版本"),time("first_response_due_at","首次回應期限"),
    time("resolution_due_at","解決期限",False),time("resolved_at","解決時間",False),txt("resolution_code","結案分類",False)
],uniques=[("case_number",)])
table("support.interaction","M13","官網、訊息、郵件、電話、現場之原始接觸與人工/AI摘要來源。","support",[
    ref("case_id","support.case_file"),txt("channel","web/line/email/phone/onsite"),txt("direction","inbound/outbound/internal"),
    ref("actor_id","core.actor",required=False),txt("external_message_id","來源訊息 ID",False),ref("content_asset_id","core.object_asset"),
    time("occurred_at","接觸時間"),txt("recording_consent_ref","錄音同意/告知參考",False),c("ai_draft","boolean","是 AI 草稿則禁止當已送出",default="false")
],["CHECK (channel IN ('web','line','email','phone','onsite'))","CHECK (direction IN ('inbound','outbound','internal'))"],[("channel","external_message_id")],True)
table("support.sla_event","M13","SLA 暫停、恢復、升級的 append-only 事件。","support",[
    ref("case_id","support.case_file"),txt("action","started/paused/resumed/escalated/met/breached"),time("occurred_at","發生時間"),ref("actor_id","core.actor"),txt("reason_code","理由")
],immutable=True)
S("support.response_draft","M13","客服回覆草稿與來源版本；草稿永遠不代表可直接對外發送。","support","queued drafted requires_human blocked failed","queued",[
    ref("case_id","support.case_file"),ref("created_by","core.actor"),ref("content_asset_id","core.object_asset",required=False),h("content_sha256","草稿精確內容hash；產生後不得原地改寫",False),
    txt("knowledge_version","核准知識庫版本"),txt("model_version","固定模型或manual"),txt("prompt_version","prompt版本或manual"),j("source_refs","核准來源UUID列表"),
    c("contains_new_investment_judgment","boolean","偵測新投資判斷時阻擋客服發送",default="false"),ref("supersedes_id","support.response_draft",required=False)
],["CHECK ((content_asset_id IS NULL) = (content_sha256 IS NULL))","CHECK (status NOT IN ('drafted','requires_human') OR content_asset_id IS NOT NULL)"])
table("support.response_approval","M13","精確草稿的核准或駁回證據；low-risk template由受控policy service記錄。","support",[
    ref("draft_id","support.response_draft"),h("draft_content_sha256","被決定之草稿hash"),txt("decision","approve/reject"),
    txt("approval_mode","human_reviewed/approved_low_risk_template"),ref("reviewer_id","core.actor"),txt("template_version","核准低風險模板版本；human-only可填manual"),
    txt("policy_version","自動回覆或人工覆核政策"),ref("evidence_asset_id","core.object_asset"),time("approved_at","決策真實時點"),time("valid_until","核定的發送期限")
],["CHECK (decision IN ('approve','reject'))","CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template'))","CHECK (valid_until>approved_at)"],immutable=True)
S("support.response_dispatch","M13","已核准客服回覆的邏輯發送，與人工記錄interaction分開。","support","queued accepted sent delivered failed unknown cancelled expired","queued",[
    ref("case_id","support.case_file"),ref("customer_id","customer.customer"),ref("draft_id","support.response_draft"),h("content_sha256","核准精確內容hash"),
    ref("channel_binding_id","delivery.channel_binding"),txt("channel","web/line/sms/email"),ref("approval_id","support.response_approval"),
    txt("approval_mode","human_reviewed/approved_low_risk_template"),txt("idempotency_key","邏輯回覆穩定key"),ref("requested_by","core.actor"),
    j("publication_reference_ids","只連結已發布且客戶有權限之內容；不可加入新投資判斷。"),txt("provider_message_ref","外部消息識別",False),
    time("accepted_at","provider接受時間",False),time("sent_at","provider確認送出",False),time("delivered_at","provider確認送達",False),time("read_at","僅可證實閱讀才填",False),
    time("valid_until","回覆不得再送出的期限"),ref("outbound_interaction_id","support.interaction",required=False)
],["CHECK (channel IN ('web','line','sms','email'))","CHECK (approval_mode IN ('human_reviewed','approved_low_risk_template'))"],[("idempotency_key",)])
table("support.response_attempt","M13","客服外送provider各次呼叫結果；未知狀態先查詢再重試。","support",[
    ref("response_id","support.response_dispatch"),c("attempt_no","integer","嘗試序號"),txt("provider","通路provider"),
    txt("provider_request_id","供應商請求/去重key"),txt("outcome","accepted/rejected/timeout/unknown"),time("started_at","呼叫開始"),time("finished_at","呼叫結束",False),txt("error_code","安全錯誤代碼",False)
],["CHECK (outcome IN ('accepted','rejected','timeout','unknown'))"],[("response_id","attempt_no")],True)
table("support.response_receipt","M13","客服發送回條不可變；accepted/sent/delivered/read 分開。","support",[
    ref("response_id","support.response_dispatch"),txt("provider","通路provider"),txt("event_id","來源回條唯一ID"),txt("provider_message_ref","provider消息ID"),
    txt("provider_status","accepted/sent/delivered/failed/read"),time("provider_occurred_at","provider真實時間"),time("received_at","平台收到時間"),
    ref("provider_receipt_asset_id","core.object_asset"),h("payload_sha256","驗證過的回條hash")
],["CHECK (provider_status IN ('accepted','sent','delivered','failed','read'))"],[("provider","event_id")],True)

S("finance.invoice","M14","應收/發票文件與稅別；不得預設所有服務都是5%營業稅。","accounting","draft issued paid voided credited","draft",[
    ref("customer_id","customer.customer"),ref("contract_id","contract.contract"),txt("invoice_number","內部或電子發票識別碼"),txt("currency","ISO currency"),
    money("untaxed_amount","經核准稅類之未稅金額"),money("tax_amount","稅額"),money("gross_amount","應付總額"),txt("tax_profile_ref","核准稅務映射版本"),
    time("issued_at","開立時間",False),time("due_at","到期時間",False)
],["CHECK (gross_amount = untaxed_amount + tax_amount)","CHECK (untaxed_amount >= 0 AND tax_amount >= 0)"],[("invoice_number",)])
table("finance.invoice_line","M14","逐服務帳款明細；升級增額不重複算原方案。","accounting",[
    ref("invoice_id","finance.invoice"),ref("contract_service_id","contract.contract_service"),c("line_no","integer","項次"),money("untaxed_amount","未稅服務額"),
    money("tax_amount","稅額"),txt("description_code","受控服務描述")
],["CHECK (untaxed_amount >= 0 AND tax_amount >= 0)"],[("invoice_id","line_no")])
S("finance.payment","M14","收款與授權；收現不直接算營收，未接受可為暫收款。","accounting","pending authorized paid failed refund_pending refunded disputed","pending",[
    ref("customer_id","customer.customer"),ref("invoice_id","finance.invoice",required=False),ref("application_id","customer.application",required=False),
    txt("provider","支付供應商"),txt("provider_transaction_id","供應商交易號"),money("amount","實際收款或授權金額"),txt("currency","幣別"),
    time("paid_at","收款時間",False),txt("payment_method_ref","tokenized method reference，禁止 PAN/CVV",False)
],["CHECK (amount >= 0)","CHECK (invoice_id IS NOT NULL OR application_id IS NOT NULL)"],[("provider","provider_transaction_id")])
table("finance.settlement","M14","支付商入帳／銀行結算，手續費分列。","accounting",[
    ref("payment_id","finance.payment"),txt("provider_settlement_id","結算唯一 ID"),money("gross_amount","原收款總額"),money("fee_amount","支付費用"),money("net_amount","銀行淨入帳"),
    txt("currency","幣別"),time("settled_at","銀行入帳時間"),txt("bank_line_ref","銀行明細引用")
],["CHECK (net_amount = gross_amount - fee_amount)","CHECK (fee_amount >= 0)"],[("provider_settlement_id",)],True)
S("finance.reconciliation_item","M14","金流、發票、銀行與總帳差異處理。","accounting","unmatched matched exception resolved","unmatched",[
    txt("source_type","payment/settlement/bank/invoice/gl"),txt("source_ref","來源識別"),ref("payment_id","finance.payment",required=False),money("difference_amount","差異金額"),
    txt("currency","幣別"),txt("reason_code","差異代碼",False),ref("resolved_by","core.actor",required=False),time("resolved_at","解決時間",False)
],uniques=[("source_type","source_ref")])
table("finance.service_schedule","M14","已接受契約的服務收入認列分期表，與收現分離。","accounting",[
    ref("contract_service_id","contract.contract_service"),c("schedule_version","integer","分期表版本"),time("period_start","本次履約區間開始"),time("period_end","區間截止"),
    money("untaxed_amount","本區間應認列未稅額"),txt("recognition_policy_version","財會核准之認列政策"),ref("approved_by","core.actor")
],["CHECK (period_end > period_start)","CHECK (untaxed_amount >= 0)"],[("contract_service_id","schedule_version","period_start")],True)
S("finance.revenue_run","M14","收入認列批次之計算、覆核及正式過帳；dry-run不可進入付款/計獎。","accounting","queued calculated review_pending approved posted failed","queued",[
    c("period_start","date","會計期間開始"),c("period_end","date","會計期間結束，exclusive"),time("cutoff_at","本批次資料截止時間"),
    txt("recognition_policy_version","財會核准認列政策版本"),c("dry_run","boolean","僅試算則不能正式過帳"),
    h("calculation_manifest_sha256","輸入資料與計算manifest hash"),txt("currency","幣別"),money("recognized_untaxed_total","本批計算收入"),
    money("deferred_remaining","剩餘預收負債"),c("entry_count","integer","本批明細數",default="0"),c("exception_count","integer","未解例外數",default="0"),
    ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),time("approved_at","覆核時間",False),time("posted_at","過帳時間",False),txt("run_key","輸入與政策的去重key")
],["CHECK (period_end > period_start)","CHECK (approved_by IS NULL OR approved_by<>prepared_by)","CHECK (entry_count>=0 AND exception_count>=0)","CHECK (status NOT IN ('approved','posted') OR (NOT dry_run AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND exception_count=0))"],[("run_key",)])
table("finance.revenue_recognition","M14","財會確認的收入／更正；獎金只消費 eligible 收入。","accounting",[
    ref("run_id","finance.revenue_run"),ref("schedule_id","finance.service_schedule"),txt("recognition_key","認列去重 key"),time("recognized_at","收入入帳時間"),money("untaxed_amount","實際已履約未稅收入；更正以新列負數"),
    txt("currency","幣別"),ref("confirmed_by","core.actor"),c("commission_eligible","boolean","可計獎標記"),ref("reverses_id","finance.revenue_recognition",required=False),
    txt("policy_version","認列政策")
],uniques=[("recognition_key",)],immutable=True)
S("finance.refund","M14","退款簽核與 provider 結果；失敗及未知不可當成功。","accounting","requested checking approval_pending approved submitted confirmed failed reconciliation_required closed rejected","requested",[
    ref("payment_id","finance.payment"),ref("customer_id","customer.customer"),ref("case_id","support.case_file",required=False),money("amount","退款實際金額"),
    txt("currency","幣別"),ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),txt("provider_refund_id","provider 退款交易",False),
    time("confirmed_at","provider 已證實退款時間",False),txt("reason_code","退款理由"),txt("idempotency_key","固定退款命令 key")
],["CHECK (amount > 0)","CHECK (approved_by IS NULL OR approved_by <> prepared_by)","CHECK (status NOT IN ('approved','submitted','confirmed','closed') OR approved_by IS NOT NULL)"],[("idempotency_key",)])
table("finance.refund_allocation","M14","退款拆分已認列收入沖減與未履約負債返還，避免獎金重扣。","accounting",[
    ref("refund_id","finance.refund"),ref("recognition_id","finance.revenue_recognition",required=False),txt("allocation_type","earned_revenue/unearned_prepayment/tax/fee"),
    money("amount","歸屬金額，非負"),txt("currency","幣別")
],["CHECK (allocation_type IN ('earned_revenue','unearned_prepayment','tax','fee'))","CHECK (amount >= 0)","CHECK ((allocation_type = 'earned_revenue') = (recognition_id IS NOT NULL))"],immutable=True)
S("finance.journal","M14","會計分錄憑證；posted 必須平衡且 maker/checker。","accounting","draft approved posted reversed","draft",[
    txt("journal_number","分錄編號"),txt("source_type","payment/revenue/refund/commission/adjustment"),c("source_id","uuid","來源 ID"),
    txt("currency","幣別"),c("accounting_date","date","會計日，以 Asia/Taipei 期間判斷"),ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),
    time("posted_at","過帳時間",False),ref("reverses_id","finance.journal",required=False)
],["CHECK (approved_by IS NULL OR approved_by <> prepared_by)","CHECK (status NOT IN ('approved','posted') OR approved_by IS NOT NULL)"],[("journal_number",),("source_type","source_id")])
table("finance.journal_line","M14","雙式簿記借貸明細；全憑證延遲檢查平衡。","accounting",[
    ref("journal_id","finance.journal"),c("line_no","integer","項次"),txt("account_code","財會核准科目"),money("debit","借方"),money("credit","貸方"),txt("cost_center","部門歸屬",False)
],["CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0))"],[("journal_id","line_no")])
S("finance.accounting_export","M14","匯出會計系統的批次檔與對帳回條。","accounting","prepared submitted acknowledged rejected reconciled","prepared",[
    txt("period_key","會計期間 YYYY-MM"),txt("destination","會計 ERP"),ref("asset_id","core.object_asset"),h("export_sha256","匯出 hash"),
    txt("external_batch_id","外部批號",False),ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),j("journal_ids","本批次 journal refs")
],["CHECK (approved_by IS NULL OR approved_by <> prepared_by)"],[("destination","export_sha256")])
S("finance.control_exception","M14","內控例外、補正、覆核與稽核證據索引；避免散落試算表。","audit","open assigned remediating awaiting_review closed waived","open",[
    txt("control_code","控制項代碼"),txt("resource_type","資源類型"),c("resource_id","uuid","資源 ID"),txt("severity","low/medium/high/critical"),
    ref("owner_actor_id","core.actor",required=False),ref("reviewer_id","core.actor",required=False),time("due_at","補正期限"),ref("evidence_asset_id","core.object_asset",required=False),
    txt("resolution_code","關閉或限期例外理由",False)
],["CHECK (owner_actor_id IS NULL OR reviewer_id IS NULL OR owner_actor_id <> reviewer_id)"])

table("commission.policy_version","M12","獎金演算法、來源年齡係數及未核准參數的版本化政策。","commission",[
    txt("policy_code","政策碼"),c("policy_version","integer","版本"),txt("approval_status","draft/approved/retired"),ref("proposed_by","core.actor"),
    c("base_pool_rate","numeric(9,6)","獎金池率；未核准為 null，不能偷偷預設3%。",False),
    c("marketing_year1","numeric(9,6)","用戶已定第一年係數",default="1.0"),c("marketing_year2","numeric(9,6)","第二年係數",default="0.5"),
    c("marketing_auto_tail","numeric(9,6)","第三年起合格連續自動續訂係數",default="0.25"),c("assisted_marketing_share","numeric(9,6)","介入續約行銷池比例",default="0.3"),
    c("assisted_sales_share","numeric(9,6)","介入續約業務池比例",default="0.7"),txt("decay_interaction","unresolved/marketing_only/whole_pool"),
    txt("tail_lineage_rule","unresolved / 另行核准的服務鏈規則"),time("effective_from","政策計算開始"),ref("approved_by","core.actor",required=False),time("approved_at","政策核准時間",False)
],["CHECK (approval_status IN ('draft','approved','retired'))","CHECK (base_pool_rate IS NULL OR base_pool_rate BETWEEN 0 AND 1)","CHECK (assisted_marketing_share + assisted_sales_share = 1)","CHECK (marketing_year1 BETWEEN 0 AND 1 AND marketing_year2 BETWEEN 0 AND 1 AND marketing_auto_tail BETWEEN 0 AND 1)","CHECK (approval_status <> 'approved' OR (base_pool_rate IS NOT NULL AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by<>proposed_by AND decay_interaction <> 'unresolved' AND tail_lineage_rule <> 'unresolved'))"],[("policy_code","policy_version")])
S("commission.calculation_run","M12","可重現的試算或正式計獎批次；未核准政策只可 simulation。","commission","queued calculating calculated review_pending approved rejected exported failed","queued",[
    ref("policy_version_id","commission.policy_version"),txt("mode","simulation/live"),txt("period_key","YYYY-MM"),txt("run_key","唯一計算資料與政策快照 key"),
    c("period_start","date","計獎區間開始"),c("period_end","date","計獎區間結束，exclusive"),time("revenue_cutoff_at","收入/退款資料截止"),
    h("input_sha256","輸入收入、退款、歸因、年齡快照 hash"),ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),time("approved_at","覆核時間",False),
    ref("started_by","core.actor"),time("started_at","開始"),time("completed_at","計算完成",False)
],["CHECK (mode IN ('simulation','live'))","CHECK (period_end>period_start)","CHECK (approved_by IS NULL OR approved_by<>prepared_by)","CHECK (status NOT IN ('approved','exported') OR (mode='live' AND approved_by IS NOT NULL AND approved_at IS NOT NULL))"],[("run_key",)])
table("commission.entry","M12","逐筆收入切分行銷、業務、公司留存之計算明細，原始列不可改。","commission",[
    ref("run_id","commission.calculation_run"),ref("recognition_id","finance.revenue_recognition"),ref("conversion_id","crm.conversion",required=False),
    ref("assignment_id","ownership.assignment",required=False),ref("attribution_id","crm.marketing_attribution",required=False),
    txt("beneficiary_type","marketing/sales/company"),ref("beneficiary_actor_id","core.actor",required=False),money("eligible_untaxed_amount","本筆可計獎未稅淨服務收入"),
    money("base_pool_amount","全筆獎金池"),money("award_amount","該受益者分配額"),txt("currency","幣別"),c("customer_year","integer","以核准周年時鐘計算的來源年"),
    c("age_coefficient","numeric(9,6)","實際年齡係數"),j("calculation_snapshot","政策版本、原始收入、退款、歸因證據、四捨五入與留存計算")
],["CHECK (beneficiary_type IN ('marketing','sales','company'))","CHECK (beneficiary_type <> 'sales' OR beneficiary_actor_id IS NOT NULL)","CHECK (award_amount >= 0 AND base_pool_amount >= 0)","CHECK (customer_year >= 1)"],[("run_id","recognition_id","beneficiary_type")],True)
table("commission.adjustment","M12","退款/歸因修正以原受領人與原政策沖回；不直接扣薪。","commission",[
    ref("original_entry_id","commission.entry"),ref("refund_allocation_id","finance.refund_allocation",required=False),money("adjustment_amount","有正負號之調整金額"),
    txt("currency","幣別"),txt("reason_code","refund/attribution_correction/calculation_correction"),ref("prepared_by","core.actor"),ref("approved_by","core.actor"),
    time("approved_at","覆核時間"),txt("recovery_method","ledger_offset/manual_review，不代表可自動從薪資扣除"),txt("dedupe_key","調整唯一鍵")
],["CHECK (prepared_by <> approved_by)"],[("dedupe_key",)],True)
S("commission.payout_batch","M12","正式獎金支出批次；試算結果不可付款。","commission","draft approval_pending approved exported paid reversed","draft",[
    txt("period_key","期間"),ref("prepared_by","core.actor"),ref("approved_by","core.actor",required=False),txt("currency","幣別"),money("total_amount","付款批次合計"),
    txt("payroll_export_ref","薪資/應付匯出引用",False),time("paid_at","實際付款時間",False)
],["CHECK (total_amount >= 0)","CHECK (approved_by IS NULL OR approved_by <> prepared_by)","CHECK (status NOT IN ('approved','exported','paid') OR approved_by IS NOT NULL)"])
table("commission.payout_line","M12","獎金列與付款批次關係，避免一筆獎金重複支付。","commission",[
    ref("batch_id","commission.payout_batch"),ref("entry_id","commission.entry"),money("amount","本批次支付數額"),j("adjustment_ids","本次採用的已核准 adjustment IDs")
],["CHECK (amount >= 0)"],[("entry_id",)])

def q_literal(s):
    return "'" + s.replace("'", "''") + "'"

def generate():
    schemas = list(dict.fromkeys(t["name"].split(".")[0] for t in TABLES))
    header = "-- Generated from database/tools/build_schema.py; version 1.0.\n-- PostgreSQL 16+; UTC; synthetic data only.\nBEGIN;\nSET TIME ZONE 'UTC';\n"
    sql = [header]
    sql.extend(f"CREATE SCHEMA IF NOT EXISTS {s};\n" for s in schemas)
    sql.append("""CREATE TABLE core.tenant (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION core.current_tenant_id() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION core.touch_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id THEN
    RAISE EXCEPTION 'immutable identity' USING ERRCODE='23514';
  END IF;
  NEW.version := OLD.version + 1;
  NEW.updated_at := clock_timestamp();
  RETURN NEW;
END $$;
CREATE FUNCTION core.reject_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'append-only evidence: %', TG_TABLE_NAME USING ERRCODE='23514';
END $$;
""")
    for t in TABLES:
        lines=[]
        for f in t["columns"]:
            lines.append("  " + f["name"] + " " + f["sql_type"] + (" NOT NULL" if f["required"] else "") + (" DEFAULT " + f["default"] if f["default"] is not None else ""))
        lines.extend(["  PRIMARY KEY (tenant_id, id)","  FOREIGN KEY (tenant_id) REFERENCES core.tenant(id)","  CHECK (version > 0)"])
        lines += ["  " + x for x in t["checks"]]
        lines += ["  UNIQUE (tenant_id, " + ", ".join(x) + ")" for x in t["uniques"]]
        sql.append(f"\nCREATE TABLE {t['name']} (\n" + ",\n".join(lines) + "\n);\n")
        sql.append(f"COMMENT ON TABLE {t['name']} IS {q_literal(t['purpose'])};\n")
        for f in t["columns"]:
            sql.append(f"COMMENT ON COLUMN {t['name']}.{f['name']} IS {q_literal(f['description'])};\n")
    for t in TABLES:
        for f in t["columns"]:
            if "references" in f:
                sql.append(f"ALTER TABLE {t['name']} ADD FOREIGN KEY (tenant_id, {f['name']}) REFERENCES {f['references']} (tenant_id, id) DEFERRABLE INITIALLY IMMEDIATE;\n")
    sql.append("\nCOMMIT;\n")
    (ROOT/"migrations/0001_domains.sql").write_text("".join(sql))
    security=[header]
    for t in TABLES:
        name=t["name"]
        security.append(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;\nALTER TABLE {name} FORCE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON {name} USING (tenant_id = core.current_tenant_id()) WITH CHECK (tenant_id = core.current_tenant_id());\n")
        trigger="core.reject_evidence_mutation" if t["immutable"] else "core.touch_version"
        event="UPDATE OR DELETE" if t["immutable"] else "UPDATE"
        security.append(f"CREATE TRIGGER guard_row BEFORE {event} ON {name} FOR EACH ROW EXECUTE FUNCTION {trigger}();\n")
        for f in t["columns"]:
            if "references" in f:
                index=f"ix_{name.replace('.','_')}_{f['name']}"
                security.append(f"CREATE INDEX {index} ON {name} (tenant_id, {f['name']});\n")
    security.append("ALTER TABLE core.tenant ENABLE ROW LEVEL SECURITY;\nALTER TABLE core.tenant FORCE ROW LEVEL SECURITY;\nCREATE POLICY tenant_isolation ON core.tenant USING (id = core.current_tenant_id()) WITH CHECK (id = core.current_tenant_id());\n")
    security.append("COMMIT;\n")
    (ROOT/"migrations/0002_isolation_indexes.sql").write_text("".join(security))
    (ROOT/"catalog.json").write_text(json.dumps(dict(schema_version="1.0.0",dialect="PostgreSQL 16+",tables=TABLES),ensure_ascii=False,indent=2)+"\n")
    md=["# 資料字典\n\n版本 1.0.0。由 `tools/build_schema.py` 生成，與 `migrations/0001_domains.sql` 同一來源。\n\n"
        "所有業務表的 PK 為 `(tenant_id, id)`；跨表 FK 也包含 `tenant_id`。`required=false` 的欄位可為 null；金額需使用 Decimal，API 以十進位字串傳遞。時間儲存 UTC，台灣營運日期轉換在服務層明確執行。\n\n"
        "保存類型是 `core.retention_policy.policy_code` 的分類，不是自動套用的天數。政策未核准／起算事件未發生／legal hold 未解除時禁止刪除。\n\n"]
    for t in TABLES:
        md += [f"## {t['name']}\n\n",f"**寫入負責：** {t['module']}。**用途：** {t['purpose']}\n\n**保存分類：** `{t['retention']}`。**歷史保護：** {'append-only，UPDATE/DELETE 被拒絕' if t['immutable'] else '版本化更新，version 由 DB 遞增；依服務狀態機操作'}。\n\n",
               "| 欄位 | PostgreSQL 型別 | 必填 | 分類 | 說明 |\n|---|---|---|---|---|\n"]
        for f in t["columns"]:
            desc=f["description"] + (f" FK: `{f['references']}`。" if "references" in f else "") + (f" 預設 `{f['default']}`。" if f["default"] is not None else "")
            md.append(f"| `{f['name']}` | `{f['sql_type']}` | {'是' if f['required'] else '否'} | {f['classification']} | {desc.replace('|','/')} |\n")
        if t["checks"] or t["uniques"]:
            md.append("\n**約束：**\n\n")
            md += [f"- `{x}`\n" for x in t["checks"]]
            md += [f"- UNIQUE `(tenant_id, {', '.join(x)})`。\n" for x in t["uniques"]]
        md.append("\n")
    (ROOT/"data-dictionary.md").write_text("".join(md))
    print(f"Generated {len(TABLES)} tables, {sum(len(t['columns']) for t in TABLES)} columns")

if __name__ == "__main__":
    generate()
