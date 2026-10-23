import type { OrganizationCreationView } from '@/app/eidolon/organization-creation-client'

import { subscriptionBudgetCopy } from './organization-subscription-budget'
import type { Locale } from './types'

export interface SourceCapabilityCopy {
  unknown: string
  available: string
  grants: string
  sourceUnsupported: string
  validationUnsupported: string
  validationRequired: string
  subscriptionSupport: string
  subscriptionLimitation: string
}

interface OrganizationCreationCopy {
  sourceCapability: SourceCapabilityCopy
  title: string
  description: string
  credentials: string
  limits: string
  organizationName: string
  ownerName: string
  namesHint: string
  create: string
  check: string
  retry: string
  switch: string
  receipt: string
  created: string
  selected: string
  setupNote: string
  setupKeys: string
  setupModel: string
  modelId: string
  modelHint: string
  modelDraft: string
  modelUnsupported: string
  saveModel: string
  modelStatus: Record<'saving' | 'saved' | 'failed' | 'superseded', string>
  recovery: string
  errorTitle: string
  progress: Record<'loading' | 'creating' | 'checking' | 'switching', string>
  errors: Record<NonNullable<OrganizationCreationView['error']>, string>
}

export const organizationCreationCopy: Record<Locale, OrganizationCreationCopy> = {
  en: {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['en'].support,
      subscriptionLimitation: subscriptionBudgetCopy['en'].limitation,
      unknown:
        'Current source-work capability is unknown or unavailable. This does not describe text-only organization work.',
      available: 'Scoped source-work implementation is available.',
      grants:
        'Explicit folder and tool grants are required. This does not confirm permission or readiness; current grants are checked for every assignment.',
      sourceUnsupported: 'Source reading is unsupported on this host.',
      validationUnsupported: 'Successful project validation and integration are unsupported on this host.',
      validationRequired:
        'Project validation and integration require the supported sandbox and project dependencies; readiness has not been checked.'
    },
    title: 'Create organization',
    description: 'Create an organization with its own work and settings. You choose when to switch to it.',
    credentials:
      'Configure this organization’s own OpenAI API key or Codex subscription connection in Settings. Credentials are separated by the application, not an operating-system sandbox.',
    limits: subscriptionBudgetCopy['en'].support,
    organizationName: 'Organization name',
    ownerName: 'Your display name',
    namesHint: 'Both names are required: up to 160 characters each, without control characters.',
    create: 'Create organization',
    check: 'Check creation status',
    retry: 'Retry original creation',
    switch: 'Switch to this organization',
    receipt: 'Created organization',
    created: 'Organization created. Switch explicitly when you are ready.',
    selected: 'Selected organization verified.',
    setupNote:
      'For a subscription, connect this organization in Settings → Accounts and select its Codex model in Model Settings. The shortcut below configures a direct API model. Configuration does not start work.',
    setupKeys: 'Set up OpenAI API key',
    setupModel: 'Set up OpenAI API model',
    modelId: 'OpenAI API model ID',
    modelHint: 'Enter an exact model ID for direct OpenAI API access. Saving configuration does not call the model.',
    modelDraft:
      'The model draft is kept while this dialog is open. Closing discards unsaved input. You can reopen model setup from Organizations.',
    modelUnsupported:
      'This form requires an isolated organization using direct OpenAI API access. For an existing legacy profile, use its usual Model Settings.',
    saveModel: 'Save model',
    modelStatus: {
      saving: 'Saving this organization’s model…',
      saved: 'Model saved for this organization.',
      failed: 'Could not confirm the model save. Your entry is retained; check before saving again.',
      superseded: 'The organization changed. This result was not applied to the current view.'
    },
    recovery:
      'Closing may not stop an operation already started. The saved creation request stays on this device so you can reopen this dialog from the original organization to check its result.',
    errorTitle: 'Check before continuing',
    progress: {
      loading: 'Checking organization creation support…',
      creating: 'Creating the organization…',
      checking: 'Checking the saved creation request…',
      switching: 'Switching and verifying the organization…'
    },
    errors: {
      unsupported:
        'Creation requires a supported local runtime and a verified current organization and owner. This runtime is not supported.',
      unconfigured: 'Save the current organization name and your display name first, then reopen this dialog.',
      unavailable:
        'Organization creation is unavailable. Check that a supported local runtime is connected and the current organization and owner are verified, then reopen this dialog.',
      storage:
        'Recovery information could not be read or saved on this device. Check this organization before trying again.',
      uncertain: 'The creation result is unknown. Check its status before retrying the original request.',
      selection:
        'The selected organization could not be verified. The connection may have changed. Check the current organization before continuing.',
      superseded: 'Another selection took precedence. Check the current organization before continuing.'
    }
  },
  ja: {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['ja'].support,
      subscriptionLimitation: subscriptionBudgetCopy['ja'].limitation,
      unknown:
        '現在のソース作業の対応状況は不明、または利用できません。テキストのみの組織作業の対応状況を示すものではありません。',
      available: '範囲を限定したソース作業の実装が利用可能です。',
      grants:
        'フォルダーとツールへの明示的な許可が必要です。権限や実行準備の確認ではありません。各作業で現在の許可が確認されます。',
      sourceUnsupported: 'このホストではソースの読み取りに対応していません。',
      validationUnsupported: 'このホストではプロジェクトの検証成功と統合に対応していません。',
      validationRequired:
        'プロジェクトの検証と統合には対応するサンドボックスと依存関係が必要です。実行準備は確認されていません。'
    },
    title: '組織を作成',
    description: '独自の作業と設定を持つ組織を作成します。切り替えるタイミングは自分で選べます。',
    credentials:
      '設定で、この組織専用の OpenAI API キーまたは Codex サブスクリプションを接続してください。認証情報はアプリで分離され、OS のサンドボックスではありません。',
    limits: subscriptionBudgetCopy['ja'].support,
    organizationName: '組織名',
    ownerName: 'あなたの表示名',
    namesHint: '両方の名前が必要です。それぞれ 160 文字以内で、制御文字は使用できません。',
    create: '組織を作成',
    check: '作成状況を確認',
    retry: '元の作成リクエストを再試行',
    switch: 'この組織に切り替え',
    receipt: '作成した組織',
    created: '組織を作成しました。準備ができたら切り替えてください。',
    selected: '選択した組織を確認しました。',
    setupNote:
      'サブスクリプションは「設定 → アカウント」でこの組織に接続し、モデル設定で Codex モデルを選択します。下のショートカットは直接 API モデル用です。設定だけでは作業を開始しません。',
    setupKeys: 'OpenAI API キーを設定',
    setupModel: 'OpenAI API モデルを設定',
    modelId: 'OpenAI API モデル ID',
    modelHint:
      'OpenAI API に直接アクセスするための正確なモデル ID を入力してください。設定を保存してもモデルは呼び出されません。',
    modelDraft:
      'モデルの下書きはこの画面を開いている間保持されます。閉じると未保存の入力は破棄されます。「組織」から再度設定できます。',
    modelUnsupported:
      'このフォームは OpenAI API に直接アクセスする分離された組織専用です。従来のプロファイルは通常のモデル設定を使用してください。',
    saveModel: 'モデルを保存',
    modelStatus: {
      saving: 'この組織のモデルを保存しています…',
      saved: 'この組織のモデルを保存しました。',
      failed: 'モデルの保存を確認できませんでした。入力は保持されています。再保存する前に確認してください。',
      superseded: '組織が変わりました。この結果は現在の画面に適用されていません。'
    },
    recovery:
      '閉じても開始済みの操作を停止できない場合があります。作成リクエストはこのデバイスに保存されるため、元の組織からこの画面を開き直して結果を確認できます。',
    errorTitle: '続行する前に確認',
    progress: {
      loading: '組織の作成に対応しているか確認しています…',
      creating: '組織を作成しています…',
      checking: '保存済みの作成リクエストを確認しています…',
      switching: '組織に切り替えて確認しています…'
    },
    errors: {
      unsupported:
        '作成には対応するローカルランタイムと、確認済みの現在の組織およびオーナーが必要です。このランタイムには対応していません。',
      unconfigured: '先に現在の組織名とあなたの表示名を保存してから、この画面を開き直してください。',
      unavailable:
        '組織を作成できません。対応するローカルランタイムへの接続と、現在の組織およびオーナーの確認を済ませてから、この画面を開き直してください。',
      storage:
        'このデバイスで復旧情報を読み取るか保存することができませんでした。再試行する前にこの組織を確認してください。',
      uncertain: '作成結果が不明です。元のリクエストを再試行する前に状況を確認してください。',
      selection:
        '選択した組織を確認できませんでした。接続が変わった可能性があります。続行する前に現在の組織を確認してください。',
      superseded: '別の選択が優先されました。続行する前に現在の組織を確認してください。'
    }
  },
  zh: {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['zh'].support,
      subscriptionLimitation: subscriptionBudgetCopy['zh'].limitation,
      unknown: '当前源代码工作能力未知或不可用。这不代表纯文本组织工作的支持情况。',
      available: '限定范围的源代码工作实现可用。',
      grants: '需要明确的文件夹和工具授权。这不确认权限或就绪状态；每项任务都会检查当前授权。',
      sourceUnsupported: '此主机不支持读取源代码。',
      validationUnsupported: '此主机不支持成功完成项目验证和集成。',
      validationRequired: '项目验证和集成需要受支持的沙盒及项目依赖；尚未检查就绪状态。'
    },
    title: '创建组织',
    description: '创建拥有独立工作和设置的组织。你可以自行选择何时切换。',
    credentials: '在设置中配置此组织自己的 OpenAI API 密钥或 Codex 订阅连接。凭据由应用程序隔离，并非操作系统沙盒。',
    limits: subscriptionBudgetCopy['zh'].support,
    organizationName: '组织名称',
    ownerName: '你的显示名称',
    namesHint: '两个名称均为必填，每个最多 160 个字符，不能包含控制字符。',
    create: '创建组织',
    check: '检查创建状态',
    retry: '重试原始创建请求',
    switch: '切换到此组织',
    receipt: '已创建的组织',
    created: '组织已创建。准备好后请手动切换。',
    selected: '已验证所选组织。',
    setupNote:
      '订阅请在“设置 → 账户”连接此组织，并在模型设置中选择 Codex 模型。下方快捷方式配置直接 API 模型。配置不会启动工作。',
    setupKeys: '设置 OpenAI API 密钥',
    setupModel: '设置 OpenAI API 模型',
    modelId: 'OpenAI API 模型 ID',
    modelHint: '输入直接访问 OpenAI API 所用的准确模型 ID。保存配置不会调用模型。',
    modelDraft: '此对话框打开时会保留模型草稿。关闭将丢弃未保存的输入。可从“组织”重新打开模型设置。',
    modelUnsupported: '此表单仅适用于直接访问 OpenAI API 的隔离组织。现有旧版配置请使用常规模型设置。',
    saveModel: '保存模型',
    modelStatus: {
      saving: '正在保存此组织的模型…',
      saved: '已为此组织保存模型。',
      failed: '无法确认模型已保存。输入已保留；再次保存前请先检查。',
      superseded: '组织已更改。此结果未应用到当前视图。'
    },
    recovery: '关闭可能无法停止已开始的操作。创建请求会保存在此设备上，你可以从原始组织重新打开此对话框以检查结果。',
    errorTitle: '继续前请检查',
    progress: {
      loading: '正在检查组织创建支持…',
      creating: '正在创建组织…',
      checking: '正在检查已保存的创建请求…',
      switching: '正在切换并验证组织…'
    },
    errors: {
      unsupported: '创建需要受支持的本地运行时，以及已验证的当前组织和所有者。此运行时不受支持。',
      unconfigured: '请先保存当前组织名称和你的显示名称，再重新打开此对话框。',
      unavailable: '组织创建不可用。请确认已连接受支持的本地运行时，并已验证当前组织和所有者，再重新打开此对话框。',
      storage: '无法在此设备上读取或保存恢复信息。重试前请检查此组织。',
      uncertain: '创建结果未知。重试原始请求前请检查其状态。',
      selection: '无法验证所选组织。连接可能已更改。继续前请检查当前组织。',
      superseded: '另一项选择已优先处理。继续前请检查当前组织。'
    }
  },
  'zh-hant': {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['zh-hant'].support,
      subscriptionLimitation: subscriptionBudgetCopy['zh-hant'].limitation,
      unknown: '目前原始碼工作能力未知或無法使用。這不代表純文字組織工作的支援情況。',
      available: '限定範圍的原始碼工作實作可用。',
      grants: '需要明確的資料夾和工具授權。這不確認權限或就緒狀態；每項任務都會檢查目前授權。',
      sourceUnsupported: '此主機不支援讀取原始碼。',
      validationUnsupported: '此主機不支援成功完成專案驗證和整合。',
      validationRequired: '專案驗證和整合需要受支援的沙箱及專案依賴；尚未檢查就緒狀態。'
    },
    title: '建立組織',
    description: '建立擁有獨立工作和設定的組織。你可以自行選擇何時切換。',
    credentials: '在設定中配置此組織自己的 OpenAI API 金鑰或 Codex 訂閱連線。憑據由應用程式隔離，並非作業系統沙盒。',
    limits: subscriptionBudgetCopy['zh-hant'].support,
    organizationName: '組織名稱',
    ownerName: '你的顯示名稱',
    namesHint: '兩個名稱皆為必填，每個最多 160 個字元，不可包含控制字元。',
    create: '建立組織',
    check: '檢查建立狀態',
    retry: '重試原始建立請求',
    switch: '切換至此組織',
    receipt: '已建立的組織',
    created: '組織已建立。準備好後請手動切換。',
    selected: '已驗證所選組織。',
    setupNote:
      '訂閱請在「設定 → 帳戶」連接此組織，並在模型設定中選擇 Codex 模型。下方捷徑配置直接 API 模型。設定不會啟動工作。',
    setupKeys: '設定 OpenAI API 金鑰',
    setupModel: '設定 OpenAI API 模型',
    modelId: 'OpenAI API 模型 ID',
    modelHint: '輸入直接存取 OpenAI API 所用的確切模型 ID。儲存設定不會呼叫模型。',
    modelDraft: '此對話框開啟時會保留模型草稿。關閉將捨棄未儲存的輸入。可從「組織」重新開啟模型設定。',
    modelUnsupported: '此表單僅適用於直接存取 OpenAI API 的隔離組織。現有舊版設定檔請使用一般模型設定。',
    saveModel: '儲存模型',
    modelStatus: {
      saving: '正在儲存此組織的模型…',
      saved: '已為此組織儲存模型。',
      failed: '無法確認模型已儲存。輸入已保留；再次儲存前請先檢查。',
      superseded: '組織已變更。此結果未套用至目前畫面。'
    },
    recovery: '關閉可能無法停止已開始的操作。建立請求會儲存在此裝置上，你可以從原始組織重新開啟此對話框以檢查結果。',
    errorTitle: '繼續前請檢查',
    progress: {
      loading: '正在檢查組織建立支援…',
      creating: '正在建立組織…',
      checking: '正在檢查已儲存的建立請求…',
      switching: '正在切換並驗證組織…'
    },
    errors: {
      unsupported: '建立需要受支援的本機執行階段，以及已驗證的目前組織和擁有者。此執行階段不受支援。',
      unconfigured: '請先儲存目前組織名稱和你的顯示名稱，再重新開啟此對話框。',
      unavailable: '無法建立組織。請確認已連接受支援的本機執行階段，並已驗證目前組織和擁有者，再重新開啟此對話框。',
      storage: '無法在此裝置上讀取或儲存復原資訊。重試前請檢查此組織。',
      uncertain: '建立結果未知。重試原始請求前請檢查其狀態。',
      selection: '無法驗證所選組織。連線可能已變更。繼續前請檢查目前組織。',
      superseded: '另一項選取已優先處理。繼續前請檢查目前組織。'
    }
  },
  ar: {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['ar'].support,
      subscriptionLimitation: subscriptionBudgetCopy['ar'].limitation,
      unknown: 'قدرة العمل على المصدر حاليًا غير معروفة أو غير متاحة. هذا لا يصف عمل المؤسسة النصي فقط.',
      available: 'يتوفر تنفيذ العمل على المصدر ضمن نطاق محدد.',
      grants:
        'يلزم منح صلاحيات صريحة للمجلدات والأدوات. هذا لا يؤكد الإذن أو الجاهزية؛ تُفحص الصلاحيات الحالية لكل مهمة.',
      sourceUnsupported: 'قراءة المصدر غير مدعومة على هذا المضيف.',
      validationUnsupported: 'إكمال التحقق من المشروع ودمجه بنجاح غير مدعوم على هذا المضيف.',
      validationRequired: 'يتطلب التحقق من المشروع ودمجه بيئة العزل المدعومة واعتماديات المشروع؛ لم تُفحص الجاهزية.'
    },
    title: 'إنشاء مؤسسة',
    description: 'أنشئ مؤسسة لها أعمالها وإعداداتها الخاصة. أنت تختار وقت التبديل إليها.',
    credentials:
      'اضبط مفتاح OpenAI API أو اتصال اشتراك Codex الخاص بهذه المؤسسة في الإعدادات. يعزل التطبيق بيانات الاعتماد، وليس صندوق حماية لنظام التشغيل.',
    limits: subscriptionBudgetCopy['ar'].support,
    organizationName: 'اسم المؤسسة',
    ownerName: 'اسمك المعروض',
    namesHint: 'الاسمان مطلوبان: حتى 160 محرفًا لكل اسم، دون محارف تحكم.',
    create: 'إنشاء مؤسسة',
    check: 'التحقق من حالة الإنشاء',
    retry: 'إعادة محاولة طلب الإنشاء الأصلي',
    switch: 'التبديل إلى هذه المؤسسة',
    receipt: 'المؤسسة المنشأة',
    created: 'تم إنشاء المؤسسة. بدّل إليها عندما تكون مستعدًا.',
    selected: 'تم التحقق من المؤسسة المحددة.',
    setupNote:
      'للاشتراك، اربط هذه المؤسسة في الإعدادات ← الحسابات واختر نموذج Codex في إعدادات النموذج. الاختصار أدناه يضبط نموذج API مباشرًا. الإعداد لا يبدأ العمل.',
    setupKeys: 'إعداد مفتاح OpenAI API',
    setupModel: 'إعداد نموذج OpenAI API',
    modelId: 'معرّف نموذج OpenAI API',
    modelHint: 'أدخل معرّف النموذج الدقيق للوصول المباشر إلى OpenAI API. حفظ الإعدادات لا يستدعي النموذج.',
    modelDraft:
      'تُحفظ مسودة النموذج ما دام هذا الحوار مفتوحاً. يؤدي إغلاقه إلى تجاهل الإدخال غير المحفوظ. يمكنك فتح إعداد النموذج مجدداً من المؤسسات.',
    modelUnsupported:
      'هذا النموذج مخصص لمؤسسة معزولة تستخدم OpenAI API مباشرة. للملفات الشخصية القديمة، استخدم إعدادات النموذج المعتادة.',
    saveModel: 'حفظ النموذج',
    modelStatus: {
      saving: 'جارٍ حفظ نموذج هذه المؤسسة…',
      saved: 'تم حفظ النموذج لهذه المؤسسة.',
      failed: 'تعذّر تأكيد حفظ النموذج. تم الاحتفاظ بإدخالك؛ تحقّق قبل الحفظ مجدداً.',
      superseded: 'تغيّرت المؤسسة. لم تُطبّق هذه النتيجة على العرض الحالي.'
    },
    recovery:
      'قد لا يؤدي الإغلاق إلى إيقاف عملية بدأت بالفعل. يبقى طلب الإنشاء محفوظًا على هذا الجهاز لتتمكن من فتح هذا الحوار من المؤسسة الأصلية والتحقق من النتيجة.',
    errorTitle: 'تحقق قبل المتابعة',
    progress: {
      loading: 'جارٍ التحقق من دعم إنشاء المؤسسات…',
      creating: 'جارٍ إنشاء المؤسسة…',
      checking: 'جارٍ التحقق من طلب الإنشاء المحفوظ…',
      switching: 'جارٍ التبديل إلى المؤسسة والتحقق منها…'
    },
    errors: {
      unsupported:
        'يتطلب الإنشاء بيئة تشغيل محلية مدعومة والتحقق من المؤسسة الحالية ومالكها. بيئة التشغيل هذه غير مدعومة.',
      unconfigured: 'احفظ اسم المؤسسة الحالية واسمك المعروض أولًا، ثم أعد فتح هذا الحوار.',
      unavailable:
        'إنشاء المؤسسات غير متاح. تحقق من الاتصال ببيئة تشغيل محلية مدعومة ومن هوية المؤسسة الحالية ومالكها، ثم أعد فتح هذا الحوار.',
      storage: 'تعذرت قراءة معلومات الاسترداد أو حفظها على هذا الجهاز. تحقق من هذه المؤسسة قبل المحاولة مجددًا.',
      uncertain: 'نتيجة الإنشاء غير معروفة. تحقق من حالته قبل إعادة محاولة الطلب الأصلي.',
      selection: 'تعذر التحقق من المؤسسة المحددة. ربما تغيّر الاتصال. تحقق من المؤسسة الحالية قبل المتابعة.',
      superseded: 'حصل اختيار آخر على الأولوية. تحقق من المؤسسة الحالية قبل المتابعة.'
    }
  },
  ru: {
    sourceCapability: {
      subscriptionSupport: subscriptionBudgetCopy['ru'].support,
      subscriptionLimitation: subscriptionBudgetCopy['ru'].limitation,
      unknown:
        'Текущая поддержка работы с исходным кодом неизвестна или недоступна. Это не описывает текстовую работу организации.',
      available: 'Реализация работы с исходным кодом в заданных границах доступна.',
      grants:
        'Нужны явные разрешения на папки и инструменты. Это не подтверждение доступа или готовности; текущие разрешения проверяются для каждого задания.',
      sourceUnsupported: 'Чтение исходного кода на этом хосте не поддерживается.',
      validationUnsupported: 'Успешная проверка и интеграция проекта на этом хосте не поддерживаются.',
      validationRequired:
        'Для проверки и интеграции проекта нужны поддерживаемая песочница и зависимости проекта; готовность не проверена.'
    },
    title: 'Создать организацию',
    description:
      'Создайте организацию с собственными задачами и настройками. Вы сами выбираете, когда на неё переключиться.',
    credentials:
      'Настройте собственный ключ OpenAI API или подключение подписки Codex этой организации в настройках. Учётные данные разделены приложением, а не песочницей ОС.',
    limits: subscriptionBudgetCopy['ru'].support,
    organizationName: 'Название организации',
    ownerName: 'Ваше отображаемое имя',
    namesHint: 'Оба поля обязательны: до 160 символов в каждом, без управляющих символов.',
    create: 'Создать организацию',
    check: 'Проверить статус создания',
    retry: 'Повторить исходный запрос создания',
    switch: 'Переключиться на эту организацию',
    receipt: 'Созданная организация',
    created: 'Организация создана. Переключитесь на неё, когда будете готовы.',
    selected: 'Выбранная организация проверена.',
    setupNote:
      'Для подписки подключите эту организацию в разделе «Настройки → Аккаунты» и выберите модель Codex в настройках модели. Кнопка ниже настраивает прямой API. Настройка не запускает работу.',
    setupKeys: 'Настроить ключ OpenAI API',
    setupModel: 'Настроить модель OpenAI API',
    modelId: 'Идентификатор модели OpenAI API',
    modelHint:
      'Введите точный идентификатор модели для прямого доступа к OpenAI API. Сохранение настроек не вызывает модель.',
    modelDraft:
      'Черновик модели хранится, пока открыт диалог. При закрытии несохранённый ввод удаляется. Настройку можно снова открыть через «Организации».',
    modelUnsupported:
      'Эта форма предназначена для изолированной организации с прямым доступом к OpenAI API. Для существующего обычного профиля используйте стандартные настройки модели.',
    saveModel: 'Сохранить модель',
    modelStatus: {
      saving: 'Сохраняется модель этой организации…',
      saved: 'Модель сохранена для этой организации.',
      failed:
        'Не удалось подтвердить сохранение модели. Введённое значение сохранено; проверьте результат перед повторным сохранением.',
      superseded: 'Организация изменилась. Результат не применён к текущему экрану.'
    },
    recovery:
      'Закрытие может не остановить уже начатую операцию. Запрос создания остаётся на этом устройстве: откройте этот диалог из исходной организации, чтобы проверить результат.',
    errorTitle: 'Проверьте перед продолжением',
    progress: {
      loading: 'Проверяем поддержку создания организаций…',
      creating: 'Создаём организацию…',
      checking: 'Проверяем сохранённый запрос создания…',
      switching: 'Переключаем и проверяем организацию…'
    },
    errors: {
      unsupported:
        'Для создания нужны поддерживаемая локальная среда и проверенные текущая организация и её владелец. Эта среда не поддерживается.',
      unconfigured:
        'Сначала сохраните название текущей организации и своё отображаемое имя, затем снова откройте этот диалог.',
      unavailable:
        'Создание организации недоступно. Проверьте подключение к поддерживаемой локальной среде и личности текущей организации и её владельца, затем снова откройте этот диалог.',
      storage:
        'Не удалось прочитать или сохранить данные восстановления на этом устройстве. Проверьте организацию перед повторной попыткой.',
      uncertain: 'Результат создания неизвестен. Проверьте статус перед повторной отправкой исходного запроса.',
      selection:
        'Не удалось проверить выбранную организацию. Подключение могло измениться. Проверьте текущую организацию перед продолжением.',
      superseded: 'Другой выбор получил приоритет. Проверьте текущую организацию перед продолжением.'
    }
  }
}
