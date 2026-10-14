import type { Locale } from './types'

export interface TeamOnboardingCopy {
  title: string
  intro: string
  connectModel: string
  modelBlocked: string
  modelRequired: string
  jobs: string
  goals: string
  namingStyle: string
  generate: string
  generating: string
  manual: string
  manualStart: string
  researchPreset: string
  supportIdentities: string
  retainedSupport: string
  presets: string
  reviewHint: string
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
    title: 'Build your starter team',
    intro: 'Connect a model, describe your work, then review your persistent team.',
    connectModel: 'Connect a model',
    modelBlocked: 'A usable model is required before your first request. Review model settings.',
    modelRequired: 'Connect a model before generating your team.',
    jobs: 'Jobs to handle',
    goals: 'Goals and outcomes',
    namingStyle: 'Naming style',
    generate: 'Generate team · Recommended',
    generating: 'Generating your team',
    manual: 'Set up manually · Not recommended',
    manualStart: 'Start with an empty team',
    researchPreset: 'Research and writing',
    supportIdentities: 'Retained support identities',
    retainedSupport:
      'Built-in support identities remain available. Unused default workers are retained as retired history.',
    presets: 'Starter presets',
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
    firstRequest: 'First request',
    error: 'Could not confirm setup. Retry the same action to check its outcome.',
    stale: 'Connection changed. Reopen setup before continuing.',
    authority:
      'Team design does not grant tool access, change budgets, or start tasks. The root executive uses Fixed model routing.',
    generationCost: 'Generating a preview uses your connected model and may incur provider charges.',
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
    title: '最初のチームを作成',
    intro: 'モデルを接続し、仕事内容を説明して、継続的に活動するチームを確認します。',
    connectModel: 'モデルを接続',
    modelBlocked: '最初の依頼には利用可能なモデルが必要です。モデル設定を確認してください。',
    modelRequired: 'チームを生成する前にモデルを接続してください。',
    jobs: '担当する仕事',
    goals: '目標と成果',
    namingStyle: '命名スタイル',
    generate: 'チームを生成 · 推奨',
    generating: 'チームを生成中',
    manual: '手動で設定 · 非推奨',
    manualStart: '空のチームから開始',
    researchPreset: '調査と執筆',
    supportIdentities: '保持される支援メンバー',
    retainedSupport: '組み込みの支援メンバーは保持されます。未使用の既定ワーカーは引退済みの履歴として保持されます。',
    presets: '初期プリセット',
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
    firstRequest: '最初の依頼',
    error: '設定を確認できませんでした。同じ操作を再試行して結果を確認してください。',
    stale: '接続が変更されました。続行する前に設定を開き直してください。',
    authority:
      'チームの設計では、ツールへのアクセス権の付与、予算の変更、タスクの開始は行いません。最上位エグゼクティブは固定モデルルーティングを使用します。',
    generationCost: 'プレビューの生成には接続済みのモデルを使用し、プロバイダーの料金が発生する場合があります。',
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
    title: '组建你的初始团队',
    intro: '连接模型，描述工作，然后审核你的常驻团队。',
    connectModel: '连接模型',
    modelBlocked: '首次请求前需要可用的模型。请检查模型设置。',
    modelRequired: '请先连接模型，再生成团队。',
    jobs: '需要处理的工作',
    goals: '目标与成果',
    namingStyle: '命名风格',
    generate: '生成团队 · 推荐',
    generating: '正在生成团队',
    manual: '手动设置 · 不推荐',
    manualStart: '从空团队开始',
    researchPreset: '研究与写作',
    supportIdentities: '保留的支持成员',
    retainedSupport: '保留内置支持成员。未使用的默认执行成员作为已退役历史保留。',
    presets: '初始预设',
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
    firstRequest: '首次请求',
    error: '无法确认设置。请重试同一操作以检查结果。',
    stale: '连接已更改。请重新打开设置后再继续。',
    authority: '团队设计不会授予工具访问权限、改变预算或启动任务。根执行主管使用固定模型路由。',
    generationCost: '生成预览会使用已连接的模型，可能产生提供商费用。',
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
    title: '建立你的初始團隊',
    intro: '連接模型、描述工作，然後檢視你的常駐團隊。',
    connectModel: '連接模型',
    modelBlocked: '首次請求前需要可用的模型。請檢查模型設定。',
    modelRequired: '請先連接模型，再產生團隊。',
    jobs: '需要處理的工作',
    goals: '目標與成果',
    namingStyle: '命名風格',
    generate: '產生團隊 · 建議',
    generating: '正在產生團隊',
    manual: '手動設定 · 不建議',
    manualStart: '從空團隊開始',
    researchPreset: '研究與寫作',
    supportIdentities: '保留的支援成員',
    retainedSupport: '保留內建支援成員。未使用的預設執行成員會保留為已退役歷史。',
    presets: '初始預設',
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
    firstRequest: '首次請求',
    error: '無法確認設定。請重試相同操作以確認結果。',
    stale: '連線已變更。請重新開啟設定後再繼續。',
    authority: '團隊設計不會授予工具存取權限、變更預算或啟動任務。根執行主管使用固定模型路由。',
    generationCost: '產生預覽會使用已連接的模型，可能產生供應商費用。',
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
    title: 'أنشئ فريقك الأول',
    intro: 'اربط نموذجاً، وصف عملك، ثم راجع فريقك الدائم.',
    connectModel: 'ربط نموذج',
    modelBlocked: 'يلزم نموذج قابل للاستخدام قبل طلبك الأول. راجع إعدادات النموذج.',
    modelRequired: 'اربط نموذجاً قبل توليد فريقك.',
    jobs: 'الأعمال المطلوبة',
    goals: 'الأهداف والنتائج',
    namingStyle: 'أسلوب التسمية',
    generate: 'توليد الفريق · موصى به',
    generating: 'جارٍ توليد فريقك',
    manual: 'إعداد يدوي · غير موصى به',
    manualStart: 'البدء بفريق فارغ',
    researchPreset: 'البحث والكتابة',
    supportIdentities: 'هويات الدعم المحفوظة',
    retainedSupport: 'تُحفظ هويات الدعم المدمجة. تُحفظ هويات المنفّذين الافتراضية غير المستخدمة كسجل متقاعد.',
    presets: 'إعدادات أولية جاهزة',
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
    firstRequest: 'الطلب الأول',
    error: 'تعذّر تأكيد الإعداد. أعد محاولة الإجراء نفسه للتحقق من نتيجته.',
    stale: 'تغيّر الاتصال. أعد فتح الإعداد قبل المتابعة.',
    authority:
      'تصميم الفريق لا يمنح صلاحيات الوصول إلى الأدوات ولا يغيّر الميزانيات ولا يبدأ المهام. يستخدم المسؤول التنفيذي الأعلى توجيه النماذج الثابت.',
    generationCost: 'يستخدم توليد المعاينة نموذجك المتصل وقد تترتب عليه رسوم من المزوّد.',
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
    title: 'Создайте свою первую команду',
    intro: 'Подключите модель, опишите работу и проверьте состав своей постоянной команды.',
    connectModel: 'Подключить модель',
    modelBlocked: 'Для первого запроса необходима доступная модель. Проверьте её настройки.',
    modelRequired: 'Подключите модель перед генерацией команды.',
    jobs: 'Предстоящая работа',
    goals: 'Цели и результаты',
    namingStyle: 'Стиль имён',
    generate: 'Сгенерировать команду · Рекомендуется',
    generating: 'Генерация команды',
    manual: 'Настроить вручную · Не рекомендуется',
    manualStart: 'Начать с пустой команды',
    researchPreset: 'Исследования и тексты',
    supportIdentities: 'Сохранённые вспомогательные участники',
    retainedSupport:
      'Встроенные вспомогательные участники сохраняются. Неиспользуемые исполнители сохраняются в истории как выведенные из состава.',
    presets: 'Готовые шаблоны',
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
    firstRequest: 'Первый запрос',
    error: 'Не удалось подтвердить настройку. Повторите то же действие, чтобы проверить результат.',
    stale: 'Подключение изменилось. Откройте настройку заново, прежде чем продолжить.',
    authority:
      'Создание структуры команды не предоставляет доступ к инструментам, не меняет бюджеты и не запускает задачи. Главный руководитель использует фиксированную маршрутизацию моделей.',
    generationCost:
      'Для генерации предварительного состава используется подключённая модель. Провайдер может взимать плату.',
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
