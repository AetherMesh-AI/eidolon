import type { Locale } from './types'

export interface TeamOnboardingCopy {
  setupOrganization: string
  title: string
  intro: string
  connectModel: string
  modelSettings: string
  checkConfiguration: string
  checkFailed: string
  modelBlocked: string
  modelRequired: string
  modelMissing: string
  subscriptionUnsupported: string
  appServerUnsupported: string
  generationUnsupported: string
  jobsHelp: string
  jobsExample: string
  goalsHelp: string
  goalsExample: string
  namingHelp: string
  namingExample: string
  jobs: string
  goals: string
  namingStyle: string
  generate: string
  generating: string
  improve: string
  improveJobs: string
  improveGoals: string
  improving: string
  improveHelp: string
  improveUnavailable: string
  reviewSuggestion: string
  suggestionReviewHelp: string
  original: string
  suggestion: string
  assumptions: string
  noAssumptions: string
  applySuggestion: string
  discardSuggestion: string
  manual: string
  manualStart: string
  researchPreset: string
  supportIdentities: string
  retainedSupport: string
  presets: string
  reviewHint: string
  reviewCoverage: string
  review: string
  name: string
  purpose: string
  responsibilities: string
  role: string
  manager: string
  worker: string
  reportsTo: string
  scope: string
  capabilityPlan: string
  capabilityIntegrate: string
  capabilityDraft: string
  capabilityAnalyze: string
  capabilities: string
  executive: string
  addManager: string
  addWorker: string
  remove: string
  validate: string
  create: string
  creating: string
  cancel: string
  back: string
  ready: string
  readyNote: string
  firstRequest: string
  error: string
  stale: string
  authority: string
  generationCost: string
  allowanceAvailable: string
  allowanceExhausted: string
  allowanceRunning: string
  allowanceUncertain: string
  allowanceBlocked: string
  regenerate: string
  retryRegeneration: string
  regenerateHelp: string
  previousDraftPreserved: string
  generationCancel: string
  readinessUnchecked: string
  description: string
  configured: string
  unavailable: string
  retry: string
  checking: string
  previewRequired: string
  route: string
  inheritedRoutes: string
  inherited: string
}

export const teamOnboardingCopy: Record<Locale, TeamOnboardingCopy> = {
  en: {
    setupOrganization: 'Set up organization',
    title: 'Build your starter team',
    intro: 'Review model settings, describe your work, then review your persistent team.',
    connectModel: 'Connect a model',
    modelSettings: 'Model settings',
    checkConfiguration: 'Check configuration',
    checkFailed: 'Could not check the model configuration. Try checking again; model execution remains unverified.',
    modelBlocked: 'A usable model is required before your first request. Review model settings.',
    modelRequired:
      'AI team setup is unavailable with the current model configuration. Review Model settings, or set up your team manually.',
    modelMissing:
      'Choose a model in Model settings before using AI team setup. You can still describe your work or set up a team manually.',
    subscriptionUnsupported:
      'The selected Codex subscription is not supported for AI team setup yet. You can describe your work and set up a team manually, or review Model settings. This does not test your subscription sign-in.',
    appServerUnsupported:
      'The selected Codex app-server route is not supported for AI team setup yet. You can describe your work and set up a team manually, or review Model settings.',
    generationUnsupported:
      'The selected model route is not supported for AI team setup. You can describe your work and set up a team manually, or review Model settings.',
    jobsHelp:
      'List the tasks or responsibilities you want this team to handle, including recurring work, topics and limits. This describes the team; it does not start work.',
    jobsExample: 'Research product changes each week, draft release notes, and review documentation.',
    goalsHelp:
      'Describe the successful result: deliverables, quality standards or targets. Include dates or measures only if you already have them.',
    goalsExample: 'A weekly source-linked summary and reviewed release notes ready for me to publish.',
    namingHelp: 'Optional: say how agents should be named. Leave blank to let the model choose names.',
    namingExample: 'Short, distinct names inspired by astronomy.',
    jobs: 'Work to delegate',
    goals: 'What success looks like',
    namingStyle: 'Team naming style (optional)',
    generate: 'Generate team · Recommended',
    generating: 'Generating your team',
    improve: 'Improve',
    improveJobs: 'Improve work description',
    improveGoals: 'Improve success description',
    improving: 'Improving your description',
    improveHelp:
      'Improve uses only the text in these three fields. Add a few words first; you will review the suggestion and any assumptions before applying it.',
    improveUnavailable:
      'Text improvement is unavailable on this runtime. Update the backend to use Improve; you can still edit these fields yourself.',
    reviewSuggestion: 'Review suggested wording',
    suggestionReviewHelp:
      'Your original text stays unchanged until you apply. Edit the suggestion and check any assumptions first.',
    original: 'Your original text',
    suggestion: 'Suggested text',
    assumptions: 'Assumptions to check',
    noAssumptions: 'No assumptions were flagged. Check the suggestion for accuracy.',
    applySuggestion: 'Apply suggestion',
    discardSuggestion: 'Discard suggestion',
    manual: 'Set up manually',
    manualStart: 'Start with an empty team',
    researchPreset: 'Research and writing',
    supportIdentities: 'Retained support identities',
    retainedSupport:
      'Built-in support identities remain available. Unused default workers are retained as retired history.',
    presets: 'Starter presets',
    reviewCoverage:
      'The foundation includes an executive, at least one manager and at least one worker. Review the full proposal below: each purpose explains why the role exists, and responsibilities define its duties. Independent review and built-in support remain available. Nothing is saved until you choose Create team.',
    reviewHint:
      'Give everyone a unique name, a purpose and responsibilities (one per line). Each manager needs at least one worker.',
    review: 'Review your team',
    name: 'Name',
    purpose: 'Purpose',
    responsibilities: 'Responsibilities',
    role: 'Role',
    manager: 'Manager',
    worker: 'Worker',
    reportsTo: 'Reports to',
    scope: 'Work scope',
    capabilityPlan: 'Plan work',
    capabilityIntegrate: 'Integrate results',
    capabilityDraft: 'Draft deliverables',
    capabilityAnalyze: 'Analyze information',
    capabilities: 'Capabilities',
    executive: 'Root executive · Fixed',
    addManager: 'Add manager',
    addWorker: 'Add worker',
    remove: 'Remove',
    validate: 'Review changes',
    create: 'Create team',
    creating: 'Creating your team',
    cancel: 'Cancel',
    back: 'Back',
    ready: 'Team saved',
    readyNote: 'No work has started. Review model, budget and access readiness before your first request.',
    firstRequest: 'Describe your first request',
    error: 'Could not confirm setup. Retry the same action to check its outcome.',
    stale: 'Connection changed. Reopen setup before continuing.',
    authority:
      'Team design does not grant tool access, change budgets, or start tasks. The root executive uses Fixed model routing.',
    allowanceAvailable:
      'Setup model calls remaining: {count}. Token, cost and shared limits are checked before every send.',
    allowanceExhausted: 'The setup allowance is exhausted. You can keep editing and create a reviewed team manually.',
    allowanceRunning:
      'A setup model request is already running. Check configuration to refresh its status before starting another.',
    allowanceUncertain:
      'A previous model request has an unknown outcome. Check configuration or retry that same request to reconcile it; new model calls are blocked.',
    allowanceBlocked:
      'The setup allowance could not be verified. Check configuration before starting another model request.',
    regenerate: 'Regenerate team',
    retryRegeneration: 'Check regeneration',
    previousDraftPreserved:
      'The recovered proposal used an earlier draft. Your newer edits are unchanged. Review this team, or request a new proposal if setup allowance remains.',
    regenerateHelp:
      'Request a different team from the same descriptions. This uses another call from the shared setup allowance and may incur provider charges. Your current proposal stays until a new one succeeds.',
    generationCost:
      'Improve and Generate team share at most three model calls for setup; your configured limits may be lower. Each request uses the selected provider and may incur charges.',
    generationCancel:
      'Closing this preview does not stop a model request already sent; provider charges may still apply.',
    readinessUnchecked: 'Model execution has not been tested. Review readiness before requesting work.',
    description: 'Describe the jobs and outcomes this team should handle.',
    configured: 'A team already exists. Manage it in Organization.',
    unavailable: 'Team setup is unavailable on this connection.',
    retry: 'Try again',
    checking: 'Checking team setup',
    previewRequired: 'Review the updated team before creating it.',
    route: 'Routing',
    inheritedRoutes:
      'New team members inherit the profile model. Separate owner assignments are needed to use Fixed or Auto catalogue routes.',
    inherited: 'Existing model and allowance settings are retained.'
  },
  ja: {
    setupOrganization: '組織を設定',
    title: '最初のチームを作成',
    intro: 'モデル設定を確認し、仕事内容を説明して、継続的に活動するチームを確認します。',
    connectModel: 'モデルを接続',
    modelSettings: 'モデル設定',
    checkConfiguration: '設定を確認',
    checkFailed: 'モデル設定を確認できませんでした。もう一度確認してください。モデルの実行は未検証です。',
    modelBlocked: '最初の依頼には利用可能なモデルが必要です。モデル設定を確認してください。',
    modelRequired:
      '現在のモデル設定ではAIによるチーム設定を利用できません。「モデル設定」を確認するか、手動で設定してください。',
    modelMissing:
      'AIでチームを設定する前に「モデル設定」でモデルを選択してください。仕事内容の入力や手動でのチーム設定は可能です。',
    subscriptionUnsupported:
      '選択したCodexサブスクリプションは、AIによるチーム設定にまだ対応していません。仕事内容を入力して手動で設定するか、「モデル設定」を確認してください。サブスクリプションへのログイン状態は検証していません。',
    appServerUnsupported:
      '選択したCodex app-server経路は、AIによるチーム設定にまだ対応していません。仕事内容を入力して手動で設定するか、「モデル設定」を確認してください。',
    generationUnsupported:
      '選択したモデル経路は、AIによるチーム設定に対応していません。仕事内容を入力して手動で設定するか、「モデル設定」を確認してください。',
    jobsHelp:
      'このチームに任せたい作業や担当業務を、定期的な作業、分野、制約も含めて記入してください。チームの役割を説明するもので、作業は開始されません。',
    jobsExample: '製品の変更を毎週調査し、リリースノートを作成して、ドキュメントを確認する。',
    goalsHelp:
      '成功した状態を、成果物、品質基準、目標などで説明してください。日付や数値は決まっている場合に記入してください。',
    goalsExample: '出典リンク付きの週次まとめと、私が公開できる確認済みのリリースノート。',
    namingHelp: '任意：エージェントの名前の付け方を指定できます。空欄の場合はモデルが名前を選びます。',
    namingExample: '天文学にちなんだ、短く区別しやすい名前。',
    jobs: '任せたい仕事',
    goals: '成功したときの成果',
    namingStyle: 'チームの命名スタイル（任意）',
    generate: 'チームを生成 · 推奨',
    generating: 'チームを生成中',
    improve: '改善',
    improveJobs: '仕事内容を改善',
    improveGoals: '成果の説明を改善',
    improving: '説明を改善中',
    improveHelp:
      '改善にはこの3つの入力欄の内容のみを使用します。まず短い説明を入力してください。提案と仮定を確認してから適用できます。',
    improveUnavailable:
      'このランタイムでは文章の改善を利用できません。バックエンドを更新してください。各入力欄は手動で編集できます。',
    reviewSuggestion: '提案された文章を確認',
    suggestionReviewHelp: '適用するまで元の文章は変わりません。先に提案を編集し、仮定を確認してください。',
    original: '元の文章',
    suggestion: '提案された文章',
    assumptions: '確認が必要な仮定',
    noAssumptions: '仮定の指摘はありませんでした。提案の正確さを確認してください。',
    applySuggestion: '提案を適用',
    discardSuggestion: '提案を破棄',
    manual: '手動で設定',
    manualStart: '空のチームから開始',
    researchPreset: '調査と執筆',
    supportIdentities: '保持される支援メンバー',
    retainedSupport: '組み込みの支援メンバーは保持されます。未使用の既定ワーカーは引退済みの履歴として保持されます。',
    presets: '初期プリセット',
    reviewCoverage:
      '基本構成にはエグゼクティブ、少なくとも1人のマネージャーと1人のワーカーが含まれます。以下の提案全体を確認してください。目的には役割が必要な理由、担当業務には具体的な仕事を記入します。独立したレビューと組み込みの支援機能は維持されます。「チームを作成」を選択するまで保存されません。',
    reviewHint:
      '全員に一意の名前、目的、担当業務（1行に1件）を設定してください。各マネージャーには少なくとも1人のワーカーが必要です。',
    review: 'チームを確認',
    name: '名前',
    purpose: '目的',
    responsibilities: '担当業務',
    role: '役割',
    manager: 'マネージャー',
    worker: 'ワーカー',
    reportsTo: '報告先',
    scope: '作業範囲',
    capabilityPlan: '作業を計画',
    capabilityIntegrate: '成果を統合',
    capabilityDraft: '成果物を作成',
    capabilityAnalyze: '情報を分析',
    capabilities: '能力',
    executive: '最上位エグゼクティブ · 固定',
    addManager: 'マネージャーを追加',
    addWorker: 'ワーカーを追加',
    remove: '削除',
    validate: '変更を確認',
    create: 'チームを作成',
    creating: 'チームを作成中',
    cancel: 'キャンセル',
    back: '戻る',
    ready: 'チームを保存しました',
    readyNote: '作業はまだ開始されていません。最初の依頼の前に、モデル、予算、アクセス権の準備状況を確認してください。',
    firstRequest: '最初の依頼を説明する',
    error: '設定を確認できませんでした。同じ操作を再試行して結果を確認してください。',
    stale: '接続が変更されました。続行する前に設定を開き直してください。',
    authority:
      'チームの設計では、ツールへのアクセス権の付与、予算の変更、タスクの開始は行いません。最上位エグゼクティブは固定モデルルーティングを使用します。',
    allowanceAvailable: '設定用モデル呼び出しの残り回数：{count}。送信の前にトークン、料金、共有上限を確認します。',
    allowanceExhausted: '設定用の利用枠を使い切りました。引き続き編集し、確認したチームを手動で作成できます。',
    allowanceRunning:
      '設定用のモデル要求はすでに実行中です。別の要求を開始する前に設定を確認し、状態を更新してください。',
    allowanceUncertain:
      '前のモデル要求の結果が不明です。設定を確認するか、同じ要求を再試行して結果を確認してください。新しいモデル呼び出しは制限されています。',
    allowanceBlocked: '設定用の利用枠を確認できませんでした。別のモデル要求を開始する前に設定を確認してください。',
    regenerate: 'チームを再生成',
    retryRegeneration: '再生成の結果を確認',
    previousDraftPreserved:
      '取得した提案は以前の下書きに基づいています。新しい編集内容は変更していません。このチームを確認するか、設定用利用枠が残っていれば新しい提案を依頼してください。',
    regenerateHelp:
      '同じ説明から別のチームを提案してもらいます。共有の設定用利用枠をもう1回使用し、料金が発生する場合があります。新しい提案が成功するまで現在の提案を保持します。',
    generationCost:
      '文章の改善とチームの生成で、設定用のモデル呼び出しを合計最大3回共有します。設定した上限がさらに低い場合もあります。選択したプロバイダーを使用し、料金が発生する場合があります。',
    generationCancel:
      'このプレビューを閉じても、送信済みのモデルリクエストは停止しません。プロバイダーの料金が発生する場合があります。',
    readinessUnchecked: 'モデルの実行はまだテストされていません。作業を依頼する前に準備状況を確認してください。',
    description: 'このチームが担当する仕事と、達成すべき成果を説明してください。',
    configured: 'チームはすでに存在します。「組織」で管理してください。',
    unavailable: 'この接続ではチームを設定できません。',
    retry: '再試行',
    checking: 'チームの設定を確認中',
    previewRequired: '作成する前に、更新されたチームを確認してください。',
    route: 'ルーティング',
    inheritedRoutes:
      '新しいチームメンバーはプロファイルのモデルを継承します。カタログの固定または自動ルートを使用するには、所有者による個別の割り当てが必要です。',
    inherited: '既存のモデルと利用枠の設定は維持されます。'
  },
  zh: {
    setupOrganization: '设置组织',
    title: '组建你的初始团队',
    intro: '检查模型设置，描述工作，然后审核你的常驻团队。',
    connectModel: '连接模型',
    modelSettings: '模型设置',
    checkConfiguration: '检查配置',
    checkFailed: '无法检查模型配置。请重新检查；模型执行仍未验证。',
    modelBlocked: '首次请求前需要可用的模型。请检查模型设置。',
    modelRequired: '当前模型配置无法用于 AI 团队设置。请检查“模型设置”或手动设置团队。',
    modelMissing: '使用 AI 设置团队前，请在“模型设置”中选择模型。你仍可描述工作或手动设置团队。',
    subscriptionUnsupported:
      '所选 Codex 订阅暂不支持 AI 团队设置。你可以描述工作并手动设置团队，或检查“模型设置”。此检查不会验证订阅登录状态。',
    appServerUnsupported:
      '所选 Codex app-server 路由暂不支持 AI 团队设置。你可以描述工作并手动设置团队，或检查“模型设置”。',
    generationUnsupported: '所选模型路由不支持 AI 团队设置。你可以描述工作并手动设置团队，或检查“模型设置”。',
    jobsHelp: '列出希望团队处理的任务或职责，包括日常工作、主题和限制。这用于描述团队，不会开始执行工作。',
    jobsExample: '每周研究产品变化、起草发布说明并审阅文档。',
    goalsHelp: '描述成功的结果，例如交付成果、质量标准或目标。仅在已有安排时填写日期或衡量指标。',
    goalsExample: '每周提供带来源链接的摘要，以及供我发布的已审阅发布说明。',
    namingHelp: '可选：说明如何为智能体命名。留空则由模型选择名称。',
    namingExample: '采用受天文学启发的简短、易区分名称。',
    jobs: '要委派的工作',
    goals: '成功的标准',
    namingStyle: '团队命名风格（可选）',
    generate: '生成团队 · 推荐',
    generating: '正在生成团队',
    improve: '改进',
    improveJobs: '改进工作描述',
    improveGoals: '改进成功标准描述',
    improving: '正在改进描述',
    improveHelp: '改进仅使用这三个输入框中的文字。请先输入简短描述；应用前可审核建议和假设。',
    improveUnavailable: '此运行时不支持文字改进。请更新后端以使用“改进”；你仍可手动编辑这些字段。',
    reviewSuggestion: '审核建议措辞',
    suggestionReviewHelp: '应用前，原文保持不变。请先编辑建议并检查其中的假设。',
    original: '你的原文',
    suggestion: '建议文本',
    assumptions: '需要核实的假设',
    noAssumptions: '未标记任何假设。请检查建议的准确性。',
    applySuggestion: '应用建议',
    discardSuggestion: '放弃建议',
    manual: '手动设置',
    manualStart: '从空团队开始',
    researchPreset: '研究与写作',
    supportIdentities: '保留的支持成员',
    retainedSupport: '保留内置支持成员。未使用的默认执行成员作为已退役历史保留。',
    presets: '初始预设',
    reviewCoverage:
      '基础团队包括一名主管、至少一名经理和一名执行成员。请审核下方完整建议：用途说明角色存在的理由，职责说明其工作。独立审核和内置支持仍然可用。选择“创建团队”之前不会保存。',
    reviewHint: '为每个人填写唯一名称、用途和职责（每行一项）。每位经理至少需要一名执行成员。',
    review: '审核团队',
    name: '名称',
    purpose: '用途',
    responsibilities: '职责',
    role: '角色',
    manager: '经理',
    worker: '执行成员',
    reportsTo: '汇报对象',
    scope: '工作范围',
    capabilityPlan: '规划工作',
    capabilityIntegrate: '整合结果',
    capabilityDraft: '起草交付成果',
    capabilityAnalyze: '分析信息',
    capabilities: '能力',
    executive: '根执行主管 · 固定',
    addManager: '添加经理',
    addWorker: '添加执行成员',
    remove: '移除',
    validate: '审核更改',
    create: '创建团队',
    creating: '正在创建团队',
    cancel: '取消',
    back: '返回',
    ready: '团队已保存',
    readyNote: '尚未开始任何工作。首次提出请求前，请检查模型、预算和访问权限是否就绪。',
    firstRequest: '描述你的首次请求',
    error: '无法确认设置。请重试同一操作以检查结果。',
    stale: '连接已更改。请重新打开设置后再继续。',
    authority: '团队设计不会授予工具访问权限、改变预算或启动任务。根执行主管使用固定模型路由。',
    allowanceAvailable: '剩余设置模型调用次数：{count}。每次发送前都会检查令牌、费用和共享限制。',
    allowanceExhausted: '设置额度已用尽。你可以继续编辑并手动创建审核后的团队。',
    allowanceRunning: '已有设置模型请求正在运行。开始另一请求前，请检查配置以刷新状态。',
    allowanceUncertain: '之前模型请求的结果未知。请检查配置或重试同一请求以核实结果；新的模型调用已被阻止。',
    allowanceBlocked: '无法验证设置额度。开始另一模型请求前，请检查配置。',
    regenerate: '重新生成团队',
    retryRegeneration: '检查重新生成结果',
    previousDraftPreserved:
      '恢复的方案基于较早的草稿。你的最新编辑保持不变。请审核当前团队，或在还有设置额度时请求新方案。',
    regenerateHelp:
      '使用相同描述请求另一个团队方案。这会再使用一次共享设置额度，并可能产生提供商费用。新方案成功返回前，当前方案保持不变。',
    generationCost:
      '文字改进和团队生成共享最多三次设置模型调用；你配置的限制可能更低。每次请求都使用所选提供商，并可能产生费用。',
    generationCancel: '关闭此预览不会停止已发送的模型请求，仍可能产生提供商费用。',
    readinessUnchecked: '尚未测试模型执行。请求工作前请检查就绪情况。',
    description: '描述此团队应处理的工作和应达成的成果。',
    configured: '团队已存在。请在“组织”中管理。',
    unavailable: '此连接不支持团队设置。',
    retry: '重试',
    checking: '正在检查团队设置',
    previewRequired: '创建前请审核更新后的团队。',
    route: '路由',
    inheritedRoutes: '新团队成员继承配置文件的模型。使用目录中的固定或自动路由需要所有者另行分配。',
    inherited: '保留现有的模型和额度设置。'
  },
  'zh-hant': {
    setupOrganization: '設定組織',
    title: '建立你的初始團隊',
    intro: '檢查模型設定、描述工作，然後檢視你的常駐團隊。',
    connectModel: '連接模型',
    modelSettings: '模型設定',
    checkConfiguration: '檢查設定',
    checkFailed: '無法檢查模型設定。請重新檢查；模型執行仍未驗證。',
    modelBlocked: '首次請求前需要可用的模型。請檢查模型設定。',
    modelRequired: '目前模型設定無法用於 AI 團隊設定。請檢查「模型設定」或手動設定團隊。',
    modelMissing: '使用 AI 設定團隊前，請在「模型設定」中選擇模型。你仍可描述工作或手動設定團隊。',
    subscriptionUnsupported:
      '所選 Codex 訂閱暫不支援 AI 團隊設定。你可以描述工作並手動設定團隊，或檢查「模型設定」。此檢查不會驗證訂閱登入狀態。',
    appServerUnsupported:
      '所選 Codex app-server 路由暫不支援 AI 團隊設定。你可以描述工作並手動設定團隊，或檢查「模型設定」。',
    generationUnsupported: '所選模型路由不支援 AI 團隊設定。你可以描述工作並手動設定團隊，或檢查「模型設定」。',
    jobsHelp: '列出希望團隊處理的任務或職責，包括日常工作、主題與限制。這用於描述團隊，不會開始執行工作。',
    jobsExample: '每週研究產品變化、撰寫發行說明並審閱文件。',
    goalsHelp: '描述成功的結果，例如交付成果、品質標準或目標。僅在已有安排時填寫日期或衡量指標。',
    goalsExample: '每週提供附來源連結的摘要，以及供我發布的已審閱發行說明。',
    namingHelp: '選填：說明如何為代理命名。留空則由模型選擇名稱。',
    namingExample: '採用受天文學啟發的簡短、易區分名稱。',
    jobs: '要委派的工作',
    goals: '成功的標準',
    namingStyle: '團隊命名風格（選填）',
    generate: '產生團隊 · 建議',
    generating: '正在產生團隊',
    improve: '改進',
    improveJobs: '改進工作描述',
    improveGoals: '改進成功標準描述',
    improving: '正在改進描述',
    improveHelp: '改進僅使用這三個輸入框中的文字。請先輸入簡短描述；套用前可檢視建議與假設。',
    improveUnavailable: '此執行階段不支援文字改進。請更新後端以使用「改進」；你仍可手動編輯這些欄位。',
    reviewSuggestion: '檢視建議措辭',
    suggestionReviewHelp: '套用前，原文保持不變。請先編輯建議並檢查其中的假設。',
    original: '你的原文',
    suggestion: '建議文字',
    assumptions: '需要確認的假設',
    noAssumptions: '未標記任何假設。請檢查建議的準確性。',
    applySuggestion: '套用建議',
    discardSuggestion: '捨棄建議',
    manual: '手動設定',
    manualStart: '從空團隊開始',
    researchPreset: '研究與寫作',
    supportIdentities: '保留的支援成員',
    retainedSupport: '保留內建支援成員。未使用的預設執行成員會保留為已退役歷史。',
    presets: '初始預設',
    reviewCoverage:
      '基礎團隊包括一名主管、至少一名經理與一名執行成員。請檢視下方完整建議：用途說明角色存在的理由，職責說明其工作。獨立審查與內建支援仍然可用。選擇「建立團隊」之前不會儲存。',
    reviewHint: '為每個人填寫唯一名稱、用途和職責（每行一項）。每位經理至少需要一名執行成員。',
    review: '檢視團隊',
    name: '名稱',
    purpose: '用途',
    responsibilities: '職責',
    role: '角色',
    manager: '經理',
    worker: '執行成員',
    reportsTo: '回報對象',
    scope: '工作範圍',
    capabilityPlan: '規劃工作',
    capabilityIntegrate: '整合結果',
    capabilityDraft: '撰寫交付成果',
    capabilityAnalyze: '分析資訊',
    capabilities: '能力',
    executive: '根執行主管 · 固定',
    addManager: '新增經理',
    addWorker: '新增執行成員',
    remove: '移除',
    validate: '檢視變更',
    create: '建立團隊',
    creating: '正在建立團隊',
    cancel: '取消',
    back: '返回',
    ready: '團隊已儲存',
    readyNote: '尚未開始任何工作。首次提出請求前，請檢查模型、預算和存取權限是否就緒。',
    firstRequest: '描述你的首次請求',
    error: '無法確認設定。請重試相同操作以確認結果。',
    stale: '連線已變更。請重新開啟設定後再繼續。',
    authority: '團隊設計不會授予工具存取權限、變更預算或啟動任務。根執行主管使用固定模型路由。',
    allowanceAvailable: '剩餘設定模型呼叫次數：{count}。每次傳送前都會檢查權杖、費用與共用限制。',
    allowanceExhausted: '設定額度已用盡。你可以繼續編輯並手動建立已檢視的團隊。',
    allowanceRunning: '已有設定模型請求正在執行。開始另一請求前，請檢查設定以重新整理狀態。',
    allowanceUncertain: '先前模型請求的結果未知。請檢查設定或重試相同請求以確認結果；新的模型呼叫已被阻止。',
    allowanceBlocked: '無法驗證設定額度。開始另一模型請求前，請檢查設定。',
    regenerate: '重新產生團隊',
    retryRegeneration: '檢查重新產生結果',
    previousDraftPreserved:
      '恢復的方案基於較早的草稿。你的最新編輯保持不變。請檢視目前團隊，或在還有設定額度時請求新方案。',
    regenerateHelp:
      '使用相同描述請求另一個團隊方案。這會再使用一次共用設定額度，並可能產生供應商費用。新方案成功傳回前，目前方案保持不變。',
    generationCost:
      '文字改進和團隊產生共用最多三次設定模型呼叫；你設定的限制可能更低。每次請求都使用所選供應商，並可能產生費用。',
    generationCancel: '關閉此預覽不會停止已傳送的模型請求，仍可能產生供應商費用。',
    readinessUnchecked: '尚未測試模型執行。請求工作前請檢查就緒情況。',
    description: '描述此團隊應處理的工作和應達成的成果。',
    configured: '團隊已存在。請在「組織」中管理。',
    unavailable: '此連線不支援團隊設定。',
    retry: '重試',
    checking: '正在檢查團隊設定',
    previewRequired: '建立前請檢視更新後的團隊。',
    route: '路由',
    inheritedRoutes: '新團隊成員繼承設定檔的模型。使用目錄中的固定或自動路由需要擁有者另行指派。',
    inherited: '保留現有的模型和額度設定。'
  },
  ar: {
    setupOrganization: 'إعداد المؤسسة',
    title: 'أنشئ فريقك الأول',
    intro: 'راجع إعدادات النموذج، وصف عملك، ثم راجع فريقك الدائم.',
    connectModel: 'ربط نموذج',
    modelSettings: 'إعدادات النموذج',
    checkConfiguration: 'التحقق من الإعدادات',
    checkFailed: 'تعذّر التحقق من إعدادات النموذج. حاول التحقق مجدداً؛ تشغيل النموذج لا يزال غير متحقق منه.',
    modelBlocked: 'يلزم نموذج قابل للاستخدام قبل طلبك الأول. راجع إعدادات النموذج.',
    modelRequired:
      'إعداد الفريق بالذكاء الاصطناعي غير متاح مع إعدادات النموذج الحالية. راجع إعدادات النموذج أو أعدّ الفريق يدوياً.',
    modelMissing:
      'اختر نموذجاً من إعدادات النموذج قبل استخدام الذكاء الاصطناعي لإعداد الفريق. يمكنك وصف العمل أو إعداد الفريق يدوياً.',
    subscriptionUnsupported:
      'اشتراك Codex المحدد غير مدعوم بعد لإعداد الفريق بالذكاء الاصطناعي. يمكنك وصف العمل وإعداد الفريق يدوياً أو مراجعة إعدادات النموذج. هذا لا يتحقق من تسجيل الدخول إلى اشتراكك.',
    appServerUnsupported:
      'مسار Codex app-server المحدد غير مدعوم بعد لإعداد الفريق بالذكاء الاصطناعي. يمكنك وصف العمل وإعداد الفريق يدوياً أو مراجعة إعدادات النموذج.',
    generationUnsupported:
      'مسار النموذج المحدد غير مدعوم لإعداد الفريق بالذكاء الاصطناعي. يمكنك وصف العمل وإعداد الفريق يدوياً أو مراجعة إعدادات النموذج.',
    jobsHelp:
      'اذكر المهام أو المسؤوليات التي تريد تفويضها للفريق، بما فيها العمل المتكرر والمواضيع والحدود. هذا يصف الفريق ولا يبدأ العمل.',
    jobsExample: 'بحث تغييرات المنتج أسبوعياً، وصياغة ملاحظات الإصدار، ومراجعة الوثائق.',
    goalsHelp:
      'صف النتيجة الناجحة: المخرجات أو معايير الجودة أو الأهداف. أضف المواعيد أو المقاييس فقط إذا كانت محددة لديك.',
    goalsExample: 'ملخص أسبوعي بروابط المصادر وملاحظات إصدار مراجَعة وجاهزة للنشر بواسطتي.',
    namingHelp: 'اختياري: وضّح طريقة تسمية الوكلاء. اتركه فارغاً ليختار النموذج الأسماء.',
    namingExample: 'أسماء قصيرة ومميزة مستوحاة من علم الفلك.',
    jobs: 'العمل المراد تفويضه',
    goals: 'كيف يبدو النجاح',
    namingStyle: 'أسلوب تسمية الفريق (اختياري)',
    generate: 'توليد الفريق · موصى به',
    generating: 'جارٍ توليد فريقك',
    improve: 'تحسين',
    improveJobs: 'تحسين وصف العمل',
    improveGoals: 'تحسين وصف النجاح',
    improving: 'جارٍ تحسين الوصف',
    improveHelp:
      'يستخدم التحسين النص في هذه الحقول الثلاثة فقط. أضف وصفاً قصيراً أولاً؛ ستراجع الاقتراح وأي افتراضات قبل تطبيقه.',
    improveUnavailable:
      'تحسين النص غير متاح في بيئة التشغيل هذه. حدّث الخادم لاستخدام التحسين؛ ما زال بإمكانك تعديل الحقول يدوياً.',
    reviewSuggestion: 'مراجعة الصياغة المقترحة',
    suggestionReviewHelp: 'يبقى نصك الأصلي دون تغيير حتى تطبّق الاقتراح. عدّل الاقتراح وتحقق من الافتراضات أولاً.',
    original: 'نصك الأصلي',
    suggestion: 'النص المقترح',
    assumptions: 'افتراضات تحتاج للتحقق',
    noAssumptions: 'لم تُذكر افتراضات. تحقّق من دقة الاقتراح.',
    applySuggestion: 'تطبيق الاقتراح',
    discardSuggestion: 'تجاهل الاقتراح',
    manual: 'إعداد يدوي',
    manualStart: 'البدء بفريق فارغ',
    researchPreset: 'البحث والكتابة',
    supportIdentities: 'هويات الدعم المحفوظة',
    retainedSupport: 'تُحفظ هويات الدعم المدمجة. تُحفظ هويات المنفّذين الافتراضية غير المستخدمة كسجل متقاعد.',
    presets: 'إعدادات أولية جاهزة',
    reviewCoverage:
      'يتضمن الأساس مسؤولاً تنفيذياً ومديراً واحداً على الأقل ومنفّذاً واحداً على الأقل. راجع المقترح الكامل أدناه: يوضح الغرض سبب وجود الدور وتحدد المسؤوليات مهامه. تبقى المراجعة المستقلة والدعم المدمج متاحين. لا يُحفظ شيء حتى تختار إنشاء الفريق.',
    reviewHint: 'امنح كل عضو اسماً فريداً وغرضاً ومسؤوليات (واحدة في كل سطر). يحتاج كل مدير إلى منفّذ واحد على الأقل.',
    review: 'مراجعة فريقك',
    name: 'الاسم',
    purpose: 'الغرض',
    responsibilities: 'المسؤوليات',
    role: 'الدور',
    manager: 'مدير',
    worker: 'منفّذ',
    reportsTo: 'يتبع لـ',
    scope: 'نطاق العمل',
    capabilityPlan: 'تخطيط العمل',
    capabilityIntegrate: 'دمج النتائج',
    capabilityDraft: 'صياغة المخرجات',
    capabilityAnalyze: 'تحليل المعلومات',
    capabilities: 'القدرات',
    executive: 'المسؤول التنفيذي الأعلى · ثابت',
    addManager: 'إضافة مدير',
    addWorker: 'إضافة منفّذ',
    remove: 'إزالة',
    validate: 'مراجعة التغييرات',
    create: 'إنشاء الفريق',
    creating: 'جارٍ إنشاء فريقك',
    cancel: 'إلغاء',
    back: 'رجوع',
    ready: 'تم حفظ الفريق',
    readyNote: 'لم يبدأ أي عمل. راجع جاهزية النموذج والميزانية وصلاحيات الوصول قبل طلبك الأول.',
    firstRequest: 'صِف طلبك الأول',
    error: 'تعذّر تأكيد الإعداد. أعد محاولة الإجراء نفسه للتحقق من نتيجته.',
    stale: 'تغيّر الاتصال. أعد فتح الإعداد قبل المتابعة.',
    authority:
      'تصميم الفريق لا يمنح صلاحيات الوصول إلى الأدوات ولا يغيّر الميزانيات ولا يبدأ المهام. يستخدم المسؤول التنفيذي الأعلى توجيه النماذج الثابت.',
    allowanceAvailable:
      'استدعاءات النموذج المتبقية للإعداد: {count}. تُفحص حدود الرموز والتكلفة والحدود المشتركة قبل كل إرسال.',
    allowanceExhausted: 'نفدت مخصصات الإعداد. يمكنك متابعة التحرير وإنشاء الفريق المراجَع يدوياً.',
    allowanceRunning: 'هناك طلب نموذج للإعداد قيد التنفيذ بالفعل. تحقّق من الإعدادات لتحديث حالته قبل بدء طلب آخر.',
    allowanceUncertain:
      'نتيجة طلب نموذج سابق غير معروفة. تحقّق من الإعدادات أو أعد محاولة الطلب نفسه لتأكيد نتيجته؛ الاستدعاءات الجديدة محظورة.',
    allowanceBlocked: 'تعذّر التحقق من مخصصات الإعداد. تحقّق من الإعدادات قبل بدء طلب نموذج آخر.',
    regenerate: 'إعادة توليد الفريق',
    retryRegeneration: 'التحقق من إعادة التوليد',
    previousDraftPreserved:
      'استند المقترح المستعاد إلى مسودة سابقة. لم تتغير تعديلاتك الأحدث. راجع هذا الفريق أو اطلب مقترحاً جديداً إذا بقيت مخصصات للإعداد.',
    regenerateHelp:
      'اطلب فريقاً مختلفاً من الأوصاف نفسها. يستخدم هذا استدعاءً إضافياً من مخصصات الإعداد المشتركة وقد تترتب عليه رسوم. يبقى المقترح الحالي حتى ينجح المقترح الجديد.',
    generationCost:
      'يشترك تحسين النص وتوليد الفريق في ثلاث استدعاءات للنموذج كحد أقصى للإعداد؛ وقد تكون حدودك أقل. يستخدم كل طلب المزوّد المحدد وقد تترتب عليه رسوم.',
    generationCancel: 'إغلاق هذه المعاينة لا يوقف طلب النموذج الذي أُرسل بالفعل؛ وقد تظل رسوم المزوّد سارية.',
    readinessUnchecked: 'لم يُختبر تشغيل النموذج بعد. راجع الجاهزية قبل طلب العمل.',
    description: 'صف الأعمال والنتائج التي ينبغي أن يتولاها هذا الفريق.',
    configured: 'يوجد فريق بالفعل. يمكنك إدارته في المؤسسة.',
    unavailable: 'إعداد الفريق غير متاح عبر هذا الاتصال.',
    retry: 'إعادة المحاولة',
    checking: 'جارٍ التحقق من إعداد الفريق',
    previewRequired: 'راجع الفريق المحدّث قبل إنشائه.',
    route: 'التوجيه',
    inheritedRoutes:
      'يرث أعضاء الفريق الجدد نموذج الملف الشخصي. يتطلب استخدام مسارات الكتالوج الثابتة أو التلقائية تعيينات منفصلة من المالك.',
    inherited: 'يتم الاحتفاظ بإعدادات النموذج ومخصصات الاستخدام الحالية.'
  },
  ru: {
    setupOrganization: 'Настроить организацию',
    title: 'Создайте свою первую команду',
    intro: 'Проверьте настройки модели, опишите работу и проверьте состав своей постоянной команды.',
    connectModel: 'Подключить модель',
    modelSettings: 'Настройки модели',
    checkConfiguration: 'Проверить конфигурацию',
    checkFailed:
      'Не удалось проверить конфигурацию модели. Повторите проверку; выполнение запросов моделью остаётся непроверенным.',
    modelBlocked: 'Для первого запроса необходима доступная модель. Проверьте её настройки.',
    modelRequired:
      'Настройка команды с ИИ недоступна при текущей конфигурации модели. Проверьте настройки или настройте команду вручную.',
    modelMissing:
      'Для настройки команды с ИИ выберите модель в настройках. Вы можете описать работу или настроить команду вручную.',
    subscriptionUnsupported:
      'Выбранная подписка Codex пока не поддерживается для настройки команды с ИИ. Вы можете описать работу и настроить команду вручную или проверить настройки модели. Вход в подписку при этом не проверяется.',
    appServerUnsupported:
      'Выбранный маршрут Codex app-server пока не поддерживается для настройки команды с ИИ. Вы можете описать работу и настроить команду вручную или проверить настройки модели.',
    generationUnsupported:
      'Выбранный маршрут модели не поддерживается для настройки команды с ИИ. Вы можете описать работу и настроить команду вручную или проверить настройки модели.',
    jobsHelp:
      'Перечислите задачи и обязанности команды, включая регулярную работу, темы и ограничения. Это описание команды, а не запуск работы.',
    jobsExample: 'Еженедельно исследовать изменения продукта, готовить заметки к выпуску и проверять документацию.',
    goalsHelp:
      'Опишите успешный результат: материалы, стандарты качества или цели. Указывайте сроки и показатели, только если они уже определены.',
    goalsExample:
      'Еженедельная сводка со ссылками на источники и проверенные заметки к выпуску, готовые к моей публикации.',
    namingHelp: 'Необязательно: укажите, как называть агентов. Оставьте поле пустым, чтобы модель выбрала имена.',
    namingExample: 'Короткие, различимые имена, вдохновлённые астрономией.',
    jobs: 'Работа для команды',
    goals: 'Как выглядит успех',
    namingStyle: 'Стиль имён команды (необязательно)',
    generate: 'Сгенерировать команду · Рекомендуется',
    generating: 'Генерация команды',
    improve: 'Улучшить',
    improveJobs: 'Улучшить описание работы',
    improveGoals: 'Улучшить описание успеха',
    improving: 'Улучшаем описание',
    improveHelp:
      'Улучшение использует только текст этих трёх полей. Сначала добавьте несколько слов; перед применением вы проверите предложение и допущения.',
    improveUnavailable:
      'Улучшение текста недоступно в этой версии среды. Обновите сервер; поля по-прежнему можно редактировать вручную.',
    reviewSuggestion: 'Проверить предложенный текст',
    suggestionReviewHelp:
      'Исходный текст не изменится до применения. Сначала отредактируйте предложение и проверьте допущения.',
    original: 'Ваш исходный текст',
    suggestion: 'Предложенный текст',
    assumptions: 'Допущения для проверки',
    noAssumptions: 'Допущения не отмечены. Проверьте точность предложения.',
    applySuggestion: 'Применить предложение',
    discardSuggestion: 'Отклонить предложение',
    manual: 'Настроить вручную',
    manualStart: 'Начать с пустой команды',
    researchPreset: 'Исследования и тексты',
    supportIdentities: 'Сохранённые вспомогательные участники',
    retainedSupport:
      'Встроенные вспомогательные участники сохраняются. Неиспользуемые исполнители сохраняются в истории как выведенные из состава.',
    presets: 'Готовые шаблоны',
    reviewCoverage:
      'Основа команды включает руководителя, хотя бы одного менеджера и одного исполнителя. Проверьте весь состав ниже: назначение объясняет необходимость роли, а обязанности определяют её задачи. Независимая проверка и встроенная поддержка сохраняются. Ничего не сохраняется до нажатия «Создать команду».',
    reviewHint:
      'Укажите каждому уникальное имя, назначение и обязанности (по одной на строку). Каждому менеджеру нужен хотя бы один исполнитель.',
    review: 'Проверить команду',
    name: 'Имя',
    purpose: 'Назначение',
    responsibilities: 'Обязанности',
    role: 'Роль',
    manager: 'Менеджер',
    worker: 'Исполнитель',
    reportsTo: 'Руководитель',
    scope: 'Область работы',
    capabilityPlan: 'Планировать работу',
    capabilityIntegrate: 'Объединять результаты',
    capabilityDraft: 'Готовить материалы',
    capabilityAnalyze: 'Анализировать информацию',
    capabilities: 'Возможности',
    executive: 'Главный руководитель · Фиксированная',
    addManager: 'Добавить менеджера',
    addWorker: 'Добавить исполнителя',
    remove: 'Удалить',
    validate: 'Проверить изменения',
    create: 'Создать команду',
    creating: 'Создание команды',
    cancel: 'Отмена',
    back: 'Назад',
    ready: 'Команда сохранена',
    readyNote: 'Работа ещё не началась. Перед первым запросом проверьте готовность модели, бюджета и прав доступа.',
    firstRequest: 'Опишите первый запрос',
    error: 'Не удалось подтвердить настройку. Повторите то же действие, чтобы проверить результат.',
    stale: 'Подключение изменилось. Откройте настройку заново, прежде чем продолжить.',
    authority:
      'Создание структуры команды не предоставляет доступ к инструментам, не меняет бюджеты и не запускает задачи. Главный руководитель использует фиксированную маршрутизацию моделей.',
    allowanceAvailable:
      'Осталось вызовов модели для настройки: {count}. Перед каждой отправкой проверяются лимиты токенов, стоимости и общего бюджета.',
    allowanceExhausted:
      'Лимит настройки исчерпан. Вы можете продолжить редактирование и вручную создать проверенную команду.',
    allowanceRunning:
      'Запрос модели для настройки уже выполняется. Проверьте конфигурацию, чтобы обновить статус перед новым запросом.',
    allowanceUncertain:
      'Результат предыдущего запроса неизвестен. Проверьте конфигурацию или повторите тот же запрос для сверки результата; новые вызовы заблокированы.',
    allowanceBlocked: 'Не удалось проверить лимит настройки. Проверьте конфигурацию перед новым запросом модели.',
    regenerate: 'Создать другой вариант',
    retryRegeneration: 'Проверить повторную генерацию',
    previousDraftPreserved:
      'Восстановленный вариант основан на предыдущем черновике. Ваши новые правки не изменены. Проверьте текущую команду или запросите новый вариант, если остался лимит настройки.',
    regenerateHelp:
      'Запросите другой состав по тем же описаниям. Это использует ещё один вызов из общего лимита настройки и может быть платным. Текущий вариант сохранится до успешного получения нового.',
    generationCost:
      'Улучшение текста и генерация команды делят общий лимит настройки: не более трёх вызовов модели. Ваши ограничения могут быть ниже. Каждый запрос использует выбранного провайдера и может быть платным.',
    generationCancel:
      'Закрытие этого предварительного просмотра не остановит уже отправленный запрос к модели. Провайдер по-прежнему может взимать плату.',
    readinessUnchecked: 'Выполнение запросов моделью ещё не проверено. Проверьте готовность перед запросом работы.',
    description: 'Опишите работу и результаты, за которые должна отвечать эта команда.',
    configured: 'Команда уже существует. Управляйте ею в разделе «Организация».',
    unavailable: 'Настройка команды недоступна при этом подключении.',
    retry: 'Повторить',
    checking: 'Проверка настройки команды',
    previewRequired: 'Проверьте обновлённый состав команды перед её созданием.',
    route: 'Маршрутизация',
    inheritedRoutes:
      'Новые участники наследуют модель профиля. Для фиксированных или автоматических маршрутов каталога нужны отдельные назначения владельца.',
    inherited: 'Существующие настройки модели и лимитов сохраняются.'
  }
}
