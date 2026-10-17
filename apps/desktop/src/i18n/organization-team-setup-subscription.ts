import type { Locale } from './types'

const messages = {
  allowanceAvailable: [
    'Setup calls left: {count}. Each request uses your subscription allowance; tokens and plan cost have no hard cap.',
    'セットアップの残り呼び出し数：{count}。各要求はサブスクリプション枠を使用し、トークン数・プラン費用に厳密な上限はありません。',
    '设置剩余调用：{count}。每次请求使用订阅额度；令牌及套餐费用没有严格上限。',
    '設定剩餘呼叫：{count}。每次請求使用訂閱額度；權杖及方案費用沒有嚴格上限。',
    'استدعاءات الإعداد المتبقية: {count}. يستخدم كل طلب حصة الاشتراك دون سقف صارم للرموز أو تكلفة الخطة.',
    'Осталось вызовов настройки: {count}. Каждый запрос расходует квоту подписки без жёсткого лимита токенов или стоимости плана.'
  ],
  readyForSetup: [
    'Choose Generate or Improve to make a setup request using your subscription allowance.',
    '生成または改善を選ぶと、サブスクリプション枠でセットアップ要求を行います。',
    '选择生成或润色，使用订阅额度发起设置请求。',
    '選擇產生或潤飾，以訂閱額度提出設定請求。',
    'اختر التوليد أو التحسين لإرسال طلب إعداد باستخدام حصة اشتراكك.',
    'Нажмите «Генерация» или «Улучшение», чтобы отправить запрос настройки за счёт квоты подписки.'
  ],
  sourceWarning: [
    'External configuration files can change independently. Eidolon rechecks them before applying this policy and before each request.',
    '外部設定ファイルは個別に変更される場合があります。方針の適用前と各要求の前に再確認します。',
    '外部配置文件可能独立更改。Eidolon 会在应用策略前及每次请求前重新检查。',
    '外部設定檔可能獨立變更。Eidolon 會在套用政策前及每次請求前重新檢查。',
    'يمكن أن تتغير ملفات الإعداد الخارجية بشكل مستقل. يعيد Eidolon فحصها قبل تطبيق السياسة وقبل كل طلب.',
    'Внешние файлы конфигурации могут меняться независимо. Eidolon проверяет их перед применением политики и каждым запросом.'
  ],
  title: [
    'ChatGPT plan for setup',
    'セットアップ用ChatGPTプラン',
    '使用 ChatGPT 套餐设置',
    '使用 ChatGPT 方案設定',
    'خطة ChatGPT للإعداد',
    'План ChatGPT для настройки'
  ],
  intro: [
    'Connect ChatGPT separately for Generate and Improve. Existing Codex sign-in is not used.',
    '生成と改善用にChatGPTを別途接続します。既存のCodexログインは使用しません。',
    '单独连接 ChatGPT，用于生成和润色。不会使用现有 Codex 登录。',
    '另外連結 ChatGPT 以產生及潤飾內容。不會使用現有 Codex 登入。',
    'اربط ChatGPT بشكل منفصل للتوليد والتحسين. لن يُستخدم تسجيل دخول Codex الحالي.',
    'Подключите ChatGPT отдельно для генерации и улучшения. Текущий вход в Codex не используется.'
  ],
  unsupported: [
    'This requires Eidolon running locally on a Mac. Linux, Windows and remote connections are not supported.',
    'Mac上でローカル実行するEidolonが必要です。Linux、Windows、リモート接続は未対応です。',
    '需要在 Mac 上本地运行 Eidolon。不支持 Linux、Windows 和远程连接。',
    '需要在 Mac 上本機執行 Eidolon。不支援 Linux、Windows 及遠端連線。',
    'يتطلب تشغيل Eidolon محلياً على Mac. لا يُدعم Linux أو Windows أو الاتصال البعيد.',
    'Требуется локальный Eidolon на Mac. Linux, Windows и удалённые подключения не поддерживаются.'
  ],
  inspect: ['Set up ChatGPT', 'ChatGPTを設定', '设置 ChatGPT', '設定 ChatGPT', 'إعداد ChatGPT', 'Настроить ChatGPT'],
  consent: [
    'Continue opens official ChatGPT consent in your system browser. Eidolon stores this organization’s connection in protected local storage until you disconnect.',
    '続行するとシステムブラウザでChatGPTの公式同意画面が開きます。切断するまで、この組織の接続を保護されたローカル領域に保存します。',
    '继续将在系统浏览器中打开 ChatGPT 官方授权页面。Eidolon 会将此组织的连接保存到受保护的本地存储，直到你断开连接。',
    '繼續會在系統瀏覽器開啟 ChatGPT 官方授權頁面。Eidolon 會將此組織的連線保存在受保護的本機儲存空間，直到你中斷連線。',
    'تفتح المتابعة موافقة ChatGPT الرسمية في متصفح النظام. يحفظ Eidolon اتصال هذه المؤسسة محلياً في تخزين محمي حتى تفصله.',
    'Продолжение откроет официальное согласие ChatGPT в системном браузере. Eidolon сохранит подключение этой организации в защищённом локальном хранилище до отключения.'
  ],
  consentCheck: [
    'Allow this dedicated connection to be stored locally',
    'この専用接続のローカル保存を許可する',
    '允许在本地保存此专用连接',
    '允許在本機儲存此專用連線',
    'السماح بحفظ هذا الاتصال المخصص محلياً',
    'Разрешить локальное хранение этого подключения'
  ],
  continue: [
    'Continue with ChatGPT',
    'ChatGPTで続行',
    '使用 ChatGPT 继续',
    '使用 ChatGPT 繼續',
    'المتابعة باستخدام ChatGPT',
    'Продолжить с ChatGPT'
  ],
  check: ['Check connection', '接続を確認', '检查连接', '檢查連線', 'فحص الاتصال', 'Проверить подключение'],
  pending: [
    'Finish the consent in your system browser, then check the connection.',
    'システムブラウザで同意を完了してから接続を確認してください。',
    '在系统浏览器中完成授权，然后检查连接。',
    '在系統瀏覽器完成授權，再檢查連線。',
    'أكمل الموافقة في متصفح النظام ثم افحص الاتصال.',
    'Завершите согласие в системном браузере, затем проверьте подключение.'
  ],
  cancelConsent: [
    'Cancel sign-in',
    'ログインをキャンセル',
    '取消登录',
    '取消登入',
    'إلغاء تسجيل الدخول',
    'Отменить вход'
  ],
  stored: [
    'Connection saved. Load account models to check current access.',
    '接続を保存しました。アカウントのモデルを読み込んで現在の利用可否を確認してください。',
    '连接已保存。加载账户模型以检查当前访问权限。',
    '連線已儲存。載入帳戶模型以檢查目前存取權限。',
    'حُفظ الاتصال. حمّل نماذج الحساب للتحقق من الوصول الحالي.',
    'Подключение сохранено. Загрузите модели аккаунта для проверки доступа.'
  ],
  loadModels: [
    'Load account models',
    'アカウントのモデルを読み込む',
    '加载账户模型',
    '載入帳戶模型',
    'تحميل نماذج الحساب',
    'Загрузить модели аккаунта'
  ],
  model: ['Setup model', 'セットアップ用モデル', '设置模型', '設定模型', 'نموذج الإعداد', 'Модель настройки'],
  choose: ['Choose a model', 'モデルを選択', '选择模型', '選擇模型', 'اختر نموذجاً', 'Выберите модель'],
  noModels: [
    'No compatible models were returned for this connection.',
    'この接続で使用できるモデルが返されませんでした。',
    '此连接未返回兼容模型。',
    '此連線未傳回相容模型。',
    'لم تُرجع هذه الوصلة نماذج متوافقة.',
    'Для этого подключения не получены совместимые модели.'
  ],
  timeout: [
    'Local timeout (seconds)',
    'ローカル制限時間（秒）',
    '本地超时（秒）',
    '本機逾時（秒）',
    'المهلة المحلية (ثوانٍ)',
    'Локальный тайм-аут (секунды)'
  ],
  review: [
    'Review setup policy',
    'セットアップ方針を確認',
    '审核设置策略',
    '檢視設定政策',
    'مراجعة سياسة الإعداد',
    'Проверить политику настройки'
  ],
  apply: [
    'Apply reviewed policy',
    '確認した方針を適用',
    '应用已审核策略',
    '套用已檢視政策',
    'تطبيق السياسة المراجعة',
    'Применить проверенную политику'
  ],
  ack: [
    'I accept the listed setup-only changes. Calls use my subscription allowance, with no hard token or USD cap. Usage may continue after cancellation. Execution limits and previous charges stay unchanged.',
    '表示されたセットアップ限定の変更に同意します。呼び出しはサブスクリプション枠を使用し、厳密なトークン数・USD上限はありません。キャンセル後も使用量が増える場合があります。実行上限と過去の利用分は変わりません。',
    '我接受所列的仅限设置的更改。调用使用我的订阅额度，没有严格的令牌或美元上限。取消后仍可能继续消耗额度。执行限制和以往费用保持不变。',
    '我接受所列的僅限設定變更。呼叫會使用我的訂閱額度，沒有嚴格的權杖或美元上限。取消後仍可能持續消耗額度。執行限制和先前費用維持不變。',
    'أوافق على التغييرات المذكورة للإعداد فقط. تستخدم الاستدعاءات حصة اشتراكي دون حد صارم للرموز أو الدولار. قد يستمر الاستخدام بعد الإلغاء. تبقى حدود التنفيذ والرسوم السابقة كما هي.',
    'Принимаю перечисленные изменения только для настройки. Вызовы расходуют квоту подписки без жёсткого лимита токенов или USD. Использование может продолжиться после отмены. Лимиты выполнения и прежние списания сохраняются.'
  ],
  changed: [
    'Policy saved. Load account models again before Generate or Improve.',
    '方針を保存しました。生成や改善の前にアカウントのモデルを再読み込みしてください。',
    '策略已保存。生成或润色前请重新加载账户模型。',
    '政策已儲存。產生或潤飾前請重新載入帳戶模型。',
    'حُفظت السياسة. أعد تحميل نماذج الحساب قبل التوليد أو التحسين.',
    'Политика сохранена. Снова загрузите модели аккаунта перед генерацией или улучшением.'
  ],
  nonHard: [
    'Calls and a local timeout replace the listed setup token and cost guarantees. Their historical origin is unknown. Execution settings and all previous calls are preserved.',
    '呼び出し数とローカル制限時間が、表示されたセットアップのトークン数・費用の保証に置き換わります。過去の設定経緯は不明です。実行設定と過去の呼び出しはすべて保持されます。',
    '调用次数和本地超时将替代所列的设置令牌及费用保证。其历史来源未知。执行设置和所有以往调用均保留。',
    '呼叫次數及本機逾時將取代所列的設定權杖及費用保證。其歷史來源不明。執行設定及所有先前呼叫均會保留。',
    'تحل حدود الاستدعاءات والمهلة المحلية محل ضمانات الرموز والتكلفة المذكورة للإعداد. مصدرها التاريخي غير معروف. تُحفظ إعدادات التنفيذ وكل الاستدعاءات السابقة.',
    'Лимиты вызовов и локальный тайм-аут заменят перечисленные гарантии токенов и стоимости настройки. Их историческое происхождение неизвестно. Настройки выполнения и все прежние вызовы сохраняются.'
  ],
  limits: [
    'Effective limits: Improve {improve}, Generate {generate}, total {total}; timeout {timeout}s.',
    '有効な上限：改善{improve}回、生成{generate}回、合計{total}回、制限時間{timeout}秒。',
    '实际限制：润色 {improve} 次、生成 {generate} 次、总计 {total} 次；超时 {timeout} 秒。',
    '有效限制：潤飾 {improve} 次、產生 {generate} 次、總計 {total} 次；逾時 {timeout} 秒。',
    'الحدود الفعلية: تحسين {improve}، توليد {generate}، الإجمالي {total}؛ المهلة {timeout}ث.',
    'Действующие лимиты: улучшение {improve}, генерация {generate}, всего {total}; тайм-аут {timeout} с.'
  ],
  retained: [
    'Previous calls retained: {total}, including {legacy} legacy calls. Inherited call ceiling: {ceiling}.',
    '保持する過去の呼び出し：{total}回（旧方式{legacy}回）。継承する呼び出し上限：{ceiling}回。',
    '保留以往调用：{total} 次，其中旧版调用 {legacy} 次。继承的调用上限：{ceiling}。',
    '保留先前呼叫：{total} 次，其中舊版呼叫 {legacy} 次。繼承的呼叫上限：{ceiling}。',
    'الاستدعاءات السابقة المحفوظة: {total}، منها {legacy} قديمة. سقف الاستدعاءات الموروث: {ceiling}.',
    'Сохранённые прежние вызовы: {total}, включая {legacy} прежнего режима. Наследуемый потолок вызовов: {ceiling}.'
  ],
  unknown: [
    'Subscription tokens and plan cost: unknown. No zero-cost or unlimited-usage guarantee.',
    'サブスクリプションのトークン数・費用：不明。無料や無制限の保証はありません。',
    '订阅令牌及套餐费用：未知。不保证零费用或无限使用。',
    '訂閱權杖及方案費用：未知。不保證零費用或無限使用。',
    'رموز الاشتراك وتكلفة الخطة: غير معروفة. لا يوجد ضمان لاستخدام مجاني أو غير محدود.',
    'Токены подписки и стоимость плана неизвестны. Бесплатное или неограниченное использование не гарантируется.'
  ],
  observed: [
    'Observed tokens: input {input}, output {output}, total {total}. Plan cost remains unknown.',
    '観測トークン数：入力{input}、出力{output}、合計{total}。プラン費用は不明です。',
    '观测令牌：输入 {input}、输出 {output}、总计 {total}。套餐费用仍未知。',
    '觀測權杖：輸入 {input}、輸出 {output}、總計 {total}。方案費用仍未知。',
    'الرموز المرصودة: إدخال {input}، إخراج {output}، الإجمالي {total}. تكلفة الخطة غير معروفة.',
    'Наблюдаемые токены: вход {input}, выход {output}, всего {total}. Стоимость плана неизвестна.'
  ],
  blocked: [
    'Setup cannot proceed with the current policy or uncertain request. Check status; no request will be resent automatically.',
    '現在の方針または結果が不確実な要求により続行できません。状態を確認してください。要求は自動再送されません。',
    '当前策略或结果未确定的请求阻止继续。请检查状态；不会自动重新发送请求。',
    '目前政策或結果未確定的請求阻止繼續。請檢查狀態；不會自動重送請求。',
    'لا يمكن المتابعة بسبب السياسة الحالية أو طلب غير محسوم. افحص الحالة؛ لن يعاد إرسال أي طلب تلقائياً.',
    'Текущая политика или неопределённый результат запроса блокирует настройку. Проверьте состояние; автоматической повторной отправки не будет.'
  ],
  failed: [
    'Could not verify this change. Check connection and policy status before trying again.',
    '変更を確認できませんでした。再試行する前に接続と方針の状態を確認してください。',
    '无法验证此更改。重试前请检查连接和策略状态。',
    '無法驗證此變更。重試前請檢查連線及政策狀態。',
    'تعذّر التحقق من التغيير. افحص حالة الاتصال والسياسة قبل المحاولة مجدداً.',
    'Не удалось проверить изменение. Перед повторной попыткой проверьте подключение и политику.'
  ],
  refresh: ['Refresh authorization', '認証を更新', '刷新授权', '重新整理授權', 'تحديث التفويض', 'Обновить авторизацию'],
  disconnect: [
    'Disconnect ChatGPT',
    'ChatGPTを切断',
    '断开 ChatGPT',
    '中斷 ChatGPT',
    'فصل ChatGPT',
    'Отключить ChatGPT'
  ],
  disconnectHelp: [
    'Disconnect retires this local connection. Previous calls stay charged. Remote revocation may be unavailable and is not guaranteed.',
    '切断すると、このローカル接続を無効にします。過去の呼び出しは計上されたままです。リモート取消は利用できない場合があり、保証されません。',
    '断开将停用此本地连接。以往调用仍计入费用。远程撤销可能不可用，且不保证成功。',
    '中斷會停用此本機連線。先前呼叫仍計入費用。遠端撤銷可能無法使用，且不保證成功。',
    'يُعطّل الفصل هذا الاتصال المحلي. تظل الاستدعاءات السابقة محسوبة. قد يتعذّر إلغاء التفويض البعيد، ولا يُضمن حدوثه.',
    'Отключение прекратит это локальное подключение. Прежние вызовы останутся учтены. Удалённый отзыв может быть недоступен и не гарантируется.'
  ],
  recover: [
    'Check saved operation',
    '保存された操作を確認',
    '检查已保存操作',
    '檢查已儲存操作',
    'فحص العملية المحفوظة',
    'Проверить сохранённую операцию'
  ],
  working: [
    'Checking setup…',
    'セットアップを確認中…',
    '正在检查设置…',
    '正在檢查設定…',
    'جارٍ فحص الإعداد…',
    'Проверка настройки…'
  ]
} as const

export type SetupSubscriptionCopy = { [K in keyof typeof messages]: string }

const forLocale = (index: number): SetupSubscriptionCopy =>
  Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as SetupSubscriptionCopy

export const setupSubscriptionCopy: Record<Locale, SetupSubscriptionCopy> = {
  en: forLocale(0),
  ja: forLocale(1),
  zh: forLocale(2),
  'zh-hant': forLocale(3),
  ar: forLocale(4),
  ru: forLocale(5)
}
