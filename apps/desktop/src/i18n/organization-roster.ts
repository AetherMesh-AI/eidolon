/** Persistent organization identity and context copy, kept aligned across locales. */
const copy = {
  objectiveExecutive: ['Executive owner', '担当エグゼクティブ', '负责执行主管', '負責執行主管'],
  objectiveManager: ['Responsible manager', '担当マネージャー', '负责经理', '負責經理'],
  chooseExecutive: ['Choose an executive', 'エグゼクティブを選択', '选择执行主管', '選擇執行主管'],
  chooseManager: ['Choose a manager', 'マネージャーを選択', '选择经理', '選擇經理'],
  ownershipNote: ['Choose existing leaders for this objective. The manager must report to its executive; ownership does not change tools or permissions.', 'この目標を担当する既存の責任者を選択します。マネージャーは選択したエグゼクティブの直属である必要があります。担当の変更でツールや権限は変わりません。', '为此目标选择现有负责人。经理必须向所选执行主管汇报；归属不会改变工具或权限。', '為此目標選擇現有負責人。經理必須向所選執行主管回報；歸屬不會改變工具或權限。'],
  ownershipRequired: ['Choose an active executive and one of their active managers.', '有効なエグゼクティブと、その直属の有効なマネージャーを選択してください。', '请选择活跃执行主管及其下属的活跃经理。', '請選擇啟用的執行主管及其下屬的啟用經理。'],
  introduction: ['A persistent organization: Executive → Manager → Worker. Managers own scoped responsibilities and coordinate reusable specialists across domains.', '永続的な組織：エグゼクティブ → マネージャー → ワーカー。マネージャーは担当範囲を持ち、分野をまたいで専門ワーカーと連携します。', '持久化组织：执行主管 → 经理 → 工作者。经理负责各自范围，并跨领域协调可重复使用的专业工作者。', '持久化組織：執行主管 → 經理 → 工作者。經理負責各自範圍，並跨領域協調可重複使用的專業工作者。'],
  persistenceNote: ['Idle, disabled or stopped execution does not delete an identity or its history. Roster size and simultaneous execution are separate.', '待機中・無効・実行停止でも、識別情報や履歴は削除されません。組織の人数と同時実行数は別です。', '空闲、禁用或停止执行不会删除身份或历史。成员数量与同时执行数量相互独立。', '閒置、停用或停止執行不會刪除身分或歷程。成員數量與同時執行數量相互獨立。'],
  collaborationNote: ['Managers can request help across domains. Each assignment still requires a matching worker, capability and explicit tool grants; collaboration never expands access.', 'マネージャーは他分野へ協力を依頼できます。各割り当てには適切なワーカー・機能・明示的なツール権限が必要です。連携でアクセス権は拡大しません。', '经理可请求其他领域协助。每项分配仍需匹配的工作者、能力和明确的工具授权；协作不会扩大访问权限。', '經理可請求其他領域協助。每項分配仍需符合的工作者、能力和明確的工具授權；協作不會擴大存取權限。'],
  roster: ['Persistent roster', '永続メンバー', '持久化成员', '持久化成員'],
  rosterCount: ['Roster members', '組織メンバー数', '成员数量', '成員數量'],
  workingCount: ['Currently executing', '現在実行中', '当前执行数量', '目前執行數量'],
  maxInflight: ['Concurrent execution limit', '同時実行上限', '并发执行上限', '並行執行上限'],
  legacyWorkerLimit: ['Worker limit (legacy runtime)', 'ワーカー上限（旧ランタイム）', '工作者上限（旧运行时）', '工作者上限（舊執行階段）'],
  defaultWorkers: ['Default worker pool size', '既定のワーカープール数', '默认工作者池大小', '預設工作者池大小'],
  executionState: ['Execution state', '実行状態', '执行状态', '執行狀態'],
  persistentIdentity: ['Persistent identity', '永続的な識別情報', '持久化身份', '持久化身分'],
  identityId: ['Identity ID', '識別 ID', '身份 ID', '身分 ID'],
  createdAt: ['Identity created', '識別情報の作成日', '身份创建时间', '身分建立時間'],
  purpose: ['Purpose', '目的', '职责目标', '職責目標'],
  contextSummary: ['Context summary', 'コンテキストの要約', '上下文摘要', '脈絡摘要'],
  memory: ['Personal memory', '個別の記憶', '个人记忆', '個人記憶'],
  facts: ['Facts', '事実', '事实', '事實'],
  decisions: ['Decisions', '決定', '决策', '決策'],
  lessons: ['Lessons', '学び', '经验', '經驗'],
  openQuestions: ['Open questions', '未解決の質問', '待解决问题', '待解決問題'],
  noMemory: ['Nothing recorded yet.', 'まだ記録されていません。', '尚无记录。', '尚無記錄。'],
  contextUnavailable: ['This runtime has not reported persisted context for this identity.', 'このランタイムは、この識別情報の永続コンテキストを報告していません。', '此运行时尚未报告该身份的持久化上下文。', '此執行階段尚未回報該身分的持久化脈絡。'],
  memoryNote: ['Bounded memory retained for this identity across assignments. These summaries do not grant permissions or replace exact task evidence.', '割り当てをまたいでこの識別情報に保持される、上限付きの記憶です。要約は権限の付与や正確なタスク証拠の代わりにはなりません。', '此身份跨任务保留的有界记忆。这些摘要不会授予权限，也不能替代精确的任务证据。', '此身分跨任務保留的有限記憶。這些摘要不會授予權限，也不能取代精確的任務證據。'],
  revision: ['Memory revision', '記憶の版', '记忆修订', '記憶修訂'],
  updatedAt: ['Last context update', 'コンテキストの最終更新', '上下文最近更新', '脈絡最近更新'],
  history: ['Recent identity history', 'この識別情報の最近の履歴', '近期身份历史', '近期身分歷程'],
  historyNote: ['Recent retained summaries, newest first. Full evidence stays in the organization ledger.', '新しい順の最近の保存済み要約です。完全な証拠は組織台帳に保持されます。', '近期保留摘要，按最新优先排序。完整证据保留在组织账本中。', '近期保留摘要，按最新優先排序。完整證據保留在組織帳本中。'],
  noHistory: ['No completed work recorded for this identity yet.', 'この識別情報の完了済み作業はまだ記録されていません。', '此身份尚无已完成工作记录。', '此身分尚無已完成工作記錄。'],
  evidenceIds: ['Evidence IDs', '証拠 ID', '证据 ID', '證據 ID'],
  taskId: ['Task ID', 'タスク ID', '任务 ID', '任務 ID'],
  requestType: ['Request type', 'リクエスト種別', '请求类型', '請求類型'],
  managingAgent: ['Scoped manager', '担当マネージャー', '范围经理', '範圍經理'],
  managementAssignment: ['Managing assignment', '管理する割り当て', '管理分配', '管理分配'],
  plannedAssignment: ['Planned assignment', '計画した割り当て', '规划分配', '規劃分配'],
  executive: ['Executive', 'エグゼクティブ', '执行主管', '執行主管'],
  manager: ['Manager', 'マネージャー', '经理', '經理'],
  worker: ['Worker', 'ワーカー', '工作者', '工作者'],
  owner: ['Owner', 'オーナー', '所有者', '擁有者'],
} as const

export type OrganizationRosterCopy = { [K in keyof typeof copy]: string }
const forLocale = (index: number): OrganizationRosterCopy => Object.fromEntries(Object.entries(copy).map(([key, values]) => [key, values[index]])) as OrganizationRosterCopy
export const organizationRosterEn = forLocale(0)
export const organizationRosterJa = forLocale(1)
export const organizationRosterZh = forLocale(2)
export const organizationRosterZhHant = forLocale(3)
