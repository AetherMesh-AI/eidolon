import { useI18n } from '@/i18n'

const en = {
  workOwners: 'Work by connection and profile',
  openRequests: 'Open requests',
  unknownSource: 'Unrecorded connection',
  unknownProfile: 'Unrecorded profile',
  sourceUnavailable: 'This work’s connection could not be opened. Select its connection and profile, then reopen Requests.',
  work: 'Organization work',
  running: 'running',
  queued: 'queued',
  needsYou: 'Needs You',
  lastKnown: 'Last known',
  stale: 'Includes last-known work; switch profile or reconnect to verify',
  plugins: 'Installed tools',
  legacyKanbanDisabled: 'Legacy Kanban is disabled',
  legacyKanbanHelp:
    'Your existing tasks and history remain in the Kanban store. Enable the Kanban plugin to open this board.',
  pluginSettings: 'Open plugin settings',
  artifacts: 'Artifacts',
  sources: 'Artifact sources',
  sessions: 'Session files',
  evidence: 'Organization evidence',
  sessionOrigin: 'Files and links from chat history. Opening a source returns to its original session.',
  evidenceOrigin:
    'Evidence and retained context from the current-profile organization ledger. These records keep their objective, request, and review provenance.',
  unavailable: 'Connect to the organization runtime to view its evidence.'
}

type Copy = typeof en

const copies: Record<string, Copy> = {
  en,
  ja: {
    workOwners: '接続とプロファイル別の作業',
    openRequests: 'リクエストを開く',
    unknownSource: '未記録の接続',
    unknownProfile: '未記録のプロファイル',
    sourceUnavailable: 'この作業の接続を開けません。接続とプロファイルを選び、リクエストを開き直してください。',
    work: '組織の作業',
    running: '実行中',
    queued: '待機中',
    needsYou: '要対応',
    lastKnown: '最終確認時点',
    stale: '最終確認時点の作業を含みます。プロファイルを切り替えるか再接続して確認してください',
    plugins: 'インストール済みツール',
    legacyKanbanDisabled: '従来のカンバンは無効です',
    legacyKanbanHelp:
      '既存のタスクと履歴はカンバンに保持されています。カンバンプラグインを有効にしてボードを開いてください。',
    pluginSettings: 'プラグイン設定を開く',
    artifacts: '成果物',
    sources: '成果物のソース',
    sessions: 'セッションのファイル',
    evidence: '組織の証拠',
    sessionOrigin: 'チャット履歴のファイルとリンクです。ソースを開くと元のセッションに戻ります。',
    evidenceOrigin:
      '現在のプロファイルの組織台帳にある証拠と保持されたコンテキストです。目標、リクエスト、レビューの出所を保持します。',
    unavailable: '組織のランタイムに接続して証拠を表示してください。'
  },
  zh: {
    workOwners: '按连接和配置档案显示工作',
    openRequests: '打开请求',
    unknownSource: '未记录的连接',
    unknownProfile: '未记录的配置档案',
    sourceUnavailable: '无法打开此工作的连接。请选择其连接和配置档案，然后重新打开请求。',
    work: '组织工作',
    running: '运行中',
    queued: '排队中',
    needsYou: '需要你处理',
    lastKnown: '上次已知',
    stale: '包含上次已知工作；切换配置档案或重新连接以确认',
    plugins: '已安装工具',
    legacyKanbanDisabled: '旧版看板已禁用',
    legacyKanbanHelp: '现有任务和历史记录仍保留在看板存储中。启用看板插件以打开此面板。',
    pluginSettings: '打开插件设置',
    artifacts: '产物',
    sources: '产物来源',
    sessions: '会话文件',
    evidence: '组织证据',
    sessionOrigin: '聊天记录中的文件和链接。打开来源会返回原始会话。',
    evidenceOrigin: '当前配置档案的组织账本中的证据和保留上下文。记录保留其目标、请求和审查来源。',
    unavailable: '连接组织运行时以查看证据。'
  },
  'zh-hant': {
    workOwners: '依連線與設定檔顯示工作',
    openRequests: '開啟請求',
    unknownSource: '未記錄的連線',
    unknownProfile: '未記錄的設定檔',
    sourceUnavailable: '無法開啟此工作的連線。請選擇其連線與設定檔，再重新開啟請求。',
    work: '組織工作',
    running: '執行中',
    queued: '排隊中',
    needsYou: '需要你處理',
    lastKnown: '上次已知',
    stale: '包含上次已知工作；切換設定檔或重新連線以確認',
    plugins: '已安裝工具',
    legacyKanbanDisabled: '舊版看板已停用',
    legacyKanbanHelp: '現有任務和歷史記錄仍保留在看板儲存中。啟用看板外掛以開啟此面板。',
    pluginSettings: '開啟外掛設定',
    artifacts: '產物',
    sources: '產物來源',
    sessions: '對話檔案',
    evidence: '組織證據',
    sessionOrigin: '聊天記錄中的檔案和連結。開啟來源會返回原始對話。',
    evidenceOrigin: '目前設定檔的組織帳本中的證據和保留脈絡。記錄保留其目標、請求和審查來源。',
    unavailable: '連線至組織執行環境以查看證據。'
  }
}

export function useOrganizationShellCopy(): Copy {
  const { locale } = useI18n()

  return copies[locale] ?? en
}
