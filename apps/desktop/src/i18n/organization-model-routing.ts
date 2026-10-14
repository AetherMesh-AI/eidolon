import type { Locale } from './types'

const messages = {
  heading: ['Model routing', 'モデルのルーティング', '模型路由', '模型路由', 'توجيه النماذج', 'Маршрутизация моделей'],
  assignment: ['Configured model assignment', '設定済みのモデル割り当て', '已配置的模型分配', '已設定的模型指派', 'تعيين النموذج المضبوط', 'Настроенное назначение модели'],
  fixed: ['Fixed', '固定', '固定', '固定', 'ثابت', 'Фиксированный'],
  auto: ['Auto', '自動', '自动', '自動', 'تلقائي', 'Авто'],
  legacy: ['Legacy provider route', '従来のプロバイダールート', '旧版提供商路由', '舊版提供者路由', 'مسار المزوّد السابق', 'Прежний маршрут провайдера'],
  routes: ['Configured routes', '設定済みルート', '已配置的路由', '已設定的路由', 'المسارات المضبوطة', 'Настроенные маршруты'],
  routeIds: ['Assignment route IDs', '割り当てルートID', '分配路由 ID', '指派路由 ID', 'معرّفات مسارات التعيين', 'ID назначенных маршрутов'],
  minimumEvidence: ['Minimum declared evidence level', '申告された証拠レベルの下限', '声明的最低证据级别', '聲明的最低證據等級', 'الحد الأدنى لمستوى الأدلة المعلن', 'Минимальный заявленный уровень доказательств'],
  maxFallbacks: ['Maximum configured fallbacks', '設定済みのフォールバック上限', '已配置的最大回退次数', '已設定的最大備援次數', 'الحد الأقصى للبدائل المضبوطة', 'Заданный предел резервных попыток'],
  assignmentNote: [
    'This is saved policy for future work. Actual dispatches and call reservations are recorded separately. Changing a model assignment preserves the member’s identity and memory.',
    '今後の作業に使う保存済み方針です。実際の選択と呼び出し予約は別に記録されます。モデル割り当てを変更してもメンバーの識別情報と記憶は保持されます。',
    '这是未来工作的已保存策略。实际调度和调用预留单独记录。更改模型分配会保留成员的身份和记忆。',
    '這是未來工作的已儲存策略。實際派送及呼叫保留另行記錄。變更模型指派會保留成員的身分及記憶。',
    'هذه سياسة محفوظة للعمل المستقبلي. يُسجّل التنفيذ الفعلي وحجز الاستدعاءات بشكل منفصل. يحافظ تغيير تعيين النموذج على هوية العضو وذاكرته.',
    'Это сохранённая политика для будущей работы. Фактические назначения и резервации вызовов записываются отдельно. Изменение модели сохраняет личность и память участника.'
  ],
  executiveNote: [
    'The built-in Executive uses Fixed with only explicitly configured fallbacks.',
    '組み込みのExecutiveは固定設定を使用し、明示的に設定されたフォールバックだけを許可します。',
    '内置执行主管使用固定模式，仅允许明确配置的回退路由。',
    '內建執行主管使用固定模式，僅允許明確設定的備援路由。',
    'يستخدم المدير التنفيذي المدمج الوضع الثابت مع البدائل المضبوطة صراحةً فقط.',
    'Встроенный руководитель использует фиксированный режим только с явно заданными резервными маршрутами.'
  ],
  legacyProvider: ['Legacy provider', '従来のプロバイダー', '旧版提供商', '舊版提供者', 'المزوّد السابق', 'Прежний провайдер'],
  legacyModel: ['Legacy model', '従来のモデル', '旧版模型', '舊版模型', 'النموذج السابق', 'Прежняя модель'],
  selectionMode: ['Dispatch selection mode', '選択時のモード', '调度选择模式', '派送選擇模式', 'وضع اختيار التنفيذ', 'Режим выбора при назначении'],
  legacySettings: ['Legacy provider and model settings', '従来のプロバイダー・モデル設定', '旧版提供商和模型设置', '舊版提供者與模型設定', 'إعدادات المزوّد والنموذج السابقة', 'Прежние настройки провайдера и модели'],
  ownerChatNote: [
    'Owner chat uses its separate legacy provider route. Organization Auto assignments do not select the chat model.',
    '所有者とのチャットは別の従来型プロバイダールートを使用します。組織の自動割り当てはチャットのモデルを選択しません。',
    '负责人聊天使用独立的旧版提供商路由。组织的自动分配不会选择聊天模型。',
    '負責人聊天使用獨立的舊版提供者路由。組織的自動指派不會選擇聊天模型。',
    'تستخدم محادثة المالك مسار المزوّد السابق المنفصل. لا تختار التعيينات التلقائية للمؤسسة نموذج المحادثة.',
    'Чат владельца использует отдельный прежний маршрут провайдера. Автоматические назначения организации не выбирают модель чата.'
  ],
  generation: ['Policy generation', '方針の世代', '策略代次', '策略代次', 'جيل السياسة', 'Поколение политики'],
  enabled: ['Routing policy enabled', 'ルーティング方針は有効', '路由策略已启用', '路由策略已啟用', 'سياسة التوجيه مفعّلة', 'Политика маршрутизации включена'],
  disabled: ['Routing policy disabled', 'ルーティング方針は無効', '路由策略已禁用', '路由策略已停用', 'سياسة التوجيه معطّلة', 'Политика маршрутизации выключена'],
  configuration: ['Owner configuration', '所有者による設定', '负责人配置', '負責人設定', 'إعدادات المالك', 'Настройка владельцем'],
  configurationNote: [
    'Read-only here. On the organization’s backend, use the owner CLI to inspect and update the route catalogue and assignments. The JSON file must contain model_routes and model_assignments. Review the generation before applying a change.',
    'ここでは読み取り専用です。組織のバックエンドで所有者向けCLIを使い、ルート一覧と割り当てを確認・更新してください。JSONファイルにはmodel_routesとmodel_assignmentsが必要です。変更前に世代を確認してください。',
    '此处为只读。在组织后端使用负责人 CLI 查看和更新路由目录及分配。JSON 文件必须包含 model_routes 和 model_assignments。应用更改前请核对代次。',
    '此處為唯讀。在組織後端使用負責人 CLI 檢視及更新路由目錄與指派。JSON 檔案必須包含 model_routes 和 model_assignments。套用變更前請核對代次。',
    'للقراءة فقط هنا. استخدم واجهة أوامر المالك على خادم المؤسسة لفحص دليل المسارات والتعيينات وتحديثها. يجب أن يحتوي ملف JSON على model_routes وmodel_assignments. راجع الجيل قبل تطبيق أي تغيير.',
    'Здесь доступен только просмотр. На сервере организации используйте CLI владельца для просмотра и изменения каталога маршрутов и назначений. Файл JSON должен содержать model_routes и model_assignments. Перед изменением проверьте поколение.'
  ],
  profileNote: [
    'Run these commands in the organization’s existing HERMES_HOME profile. Replace FILE.json and CHANGE_ID with your configuration file and a unique change ID. Provider credentials stay in the existing credential configuration.',
    '組織で使用中のHERMES_HOMEプロファイルで実行してください。FILE.jsonとCHANGE_IDを設定ファイルと一意の変更IDに置き換えます。認証情報は既存の認証設定で管理します。',
    '在组织现有的 HERMES_HOME 配置环境中运行这些命令。将 FILE.json 和 CHANGE_ID 替换为配置文件及唯一的更改 ID。提供商凭据仍由现有凭据配置管理。',
    '在組織現有的 HERMES_HOME 設定環境中執行這些命令。將 FILE.json 和 CHANGE_ID 替換為設定檔案及唯一的變更 ID。提供者憑證仍由現有憑證設定管理。',
    'شغّل هذه الأوامر ضمن ملف HERMES_HOME الحالي للمؤسسة. استبدل FILE.json وCHANGE_ID بملف إعداداتك ومعرّف تغيير فريد. تبقى بيانات اعتماد المزوّد ضمن إعدادات الاعتماد الحالية.',
    'Выполняйте команды в существующем профиле HERMES_HOME организации. Замените FILE.json и CHANGE_ID своим файлом настройки и уникальным ID изменения. Данные доступа провайдера остаются в текущих настройках учётных данных.'
  ],
  transportNote: [
    'Configured routes do not prove availability. Assigned routes currently support standard OpenAI and generic custom or named-custom direct chat completions only. OpenRouter, Nous, GitHub/Copilot, LM Studio, Ollama and search-backed endpoints stop before initialization. Legacy inherited providers remain separate.',
    'ルートの設定は利用可能性の証明ではありません。割り当てルートは現在、標準OpenAIと汎用のカスタム・名前付きカスタムの直接chat-completions通信だけに対応しています。OpenRouter、Nous、GitHub/Copilot、LM Studio、Ollama、検索ベースのエンドポイントは初期化前に停止します。従来の継承プロバイダーは別に保持されます。',
    '已配置的路由不代表可用性。分配路由目前仅支持标准 OpenAI 和通用自定义或命名自定义的直接 chat-completions。OpenRouter、Nous、GitHub/Copilot、LM Studio、Ollama 及搜索类端点会在初始化前停止。旧版继承提供商保持独立。',
    '已設定的路由不代表可用性。指派路由目前僅支援標準 OpenAI 和通用自訂或具名自訂的直接 chat-completions。OpenRouter、Nous、GitHub/Copilot、LM Studio、Ollama 及搜尋類端點會在初始化前停止。舊版繼承提供者保持獨立。',
    'وجود إعدادات للمسار لا يثبت توفره. تدعم المسارات المعيّنة حالياً OpenAI القياسي ونقاط chat-completions المباشرة المخصصة العامة أو المسماة فقط. تتوقف نقاط OpenRouter وNous وGitHub/Copilot وLM Studio وOllama والنقاط المعتمدة على البحث قبل التهيئة. تبقى المزوّدات السابقة الموروثة منفصلة.',
    'Настройка маршрута не доказывает его доступность. Назначенные маршруты пока поддерживают только стандартный OpenAI и обычные custom или именованные custom с прямым chat-completions. OpenRouter, Nous, GitHub/Copilot, LM Studio, Ollama и поисковые адреса останавливаются до инициализации. Прежние наследуемые провайдеры остаются отдельно.'
  ],
  fallbackNote: [
    'A configured fallback may run only after a completed, invalid structured response without tools. Unknown outcomes stop; they are not retried on another route.',
    '設定済みフォールバックは、ツールを使わない構造化応答が完了したものの無効だった場合に限ります。結果不明の場合は停止し、別ルートで再試行しません。',
    '只有在无工具的结构化响应已完成但无效时，才能使用已配置的回退路由。结果未知时会停止，不会换路由重试。',
    '只有在無工具的結構化回應已完成但無效時，才能使用已設定的備援路由。結果未知時會停止，不會換路由重試。',
    'لا يُستخدم البديل المضبوط إلا بعد اكتمال استجابة منظمة غير صالحة دون أدوات. تتوقف العملية إذا كانت النتيجة مجهولة، ولا تُعاد المحاولة بمسار آخر.',
    'Заданный резервный маршрут допускается только после завершённого некорректного структурированного ответа без инструментов. При неизвестном исходе работа останавливается без повтора на другом маршруте.'
  ],
  autoNote: [
    'Auto chooses the cheapest eligible configured route within capability, owner-declared evidence, context, output and known configured price ceilings. It makes no quality or subscription-quota guarantee.',
    '自動設定は、機能、所有者申告の証拠、コンテキスト、出力、既知の設定単価制限を満たすルートのうち最も安いものを選びます。品質やサブスクリプション残量は保証しません。',
    '自动模式在满足能力、负责人声明的证据、上下文、输出及已知配置费率限制的路由中选择最便宜的路由。它不保证质量或订阅配额。',
    '自動模式在滿足能力、負責人聲明的證據、上下文、輸出及已知設定費率限制的路由中選擇最便宜的路由。它不保證品質或訂閱配額。',
    'يختار الوضع التلقائي أقل المسارات المضبوطة المؤهلة تكلفةً ضمن متطلبات القدرات والأدلة التي يعلنها المالك والسياق والإخراج وحدود الأسعار المعروفة المضبوطة. ولا يضمن الجودة أو حصة الاشتراك.',
    'Авто выбирает самый дешёвый допустимый настроенный маршрут с учётом возможностей, заявленных владельцем доказательств, контекста, вывода и известных заданных тарифных ограничений. Гарантий качества или квоты подписки нет.'
  ],
  quotaNote: [
    'Subscription quota is unknown. Configured prices are admission estimates, not measured provider charges.',
    'サブスクリプション残量は不明です。設定単価は利用許可のための見積もりで、実測のプロバイダー請求額ではありません。',
    '订阅配额未知。配置价格是准入估算，不是测得的提供商费用。',
    '訂閱配額未知。設定價格是准入估算，不是測得的提供者費用。',
    'حصة الاشتراك غير معروفة. الأسعار المضبوطة تقديرات للسماح بالاستدعاء وليست رسوماً مقاسة من المزوّد.',
    'Квота подписки неизвестна. Настроенные цены служат оценкой для допуска, а не измеренными расходами провайдера.'
  ],
  reasoning: ['Reasoning effort', '推論強度', '推理强度', '推理強度', 'مستوى الاستدلال', 'Уровень рассуждения'],
  evidence: ['Owner-declared evidence level', '所有者申告の証拠レベル', '负责人声明的证据级别', '負責人聲明的證據等級', 'مستوى الأدلة المعلن من المالك', 'Уровень доказательств, заявленный владельцем'],
  contextOutput: ['Configured context / output token limits', '設定済みコンテキスト・出力トークン上限', '配置的上下文 / 输出令牌上限', '設定的上下文 / 輸出權杖上限', 'حدود رموز السياق / الإخراج المضبوطة', 'Заданные пределы токенов контекста / вывода'],
  priced: ['Configured prices known', '設定単価は既知', '已知配置价格', '已知設定價格', 'الأسعار المضبوطة معروفة', 'Заданные цены известны'],
  unpriced: ['Configured prices unknown', '設定単価は不明', '配置价格未知', '設定價格未知', 'الأسعار المضبوطة غير معروفة', 'Заданные цены неизвестны'],
  emptyRoutes: ['No routes configured', 'ルート未設定', '未配置路由', '未設定路由', 'لا توجد مسارات مضبوطة', 'Маршруты не настроены'],
  dispatch: ['Recorded model dispatch', '記録済みモデル選択', '已记录的模型调度', '已記錄的模型派送', 'تنفيذ النموذج المسجّل', 'Записанное назначение модели'],
  dispatches: ['Recorded model dispatches', '記録済みモデル選択履歴', '已记录的模型调度历史', '已記錄的模型派送歷程', 'تنفيذات النماذج المسجّلة', 'Записанные назначения моделей'],
  dispatchNote: [
    'Immutable selection recorded for this attempt. This does not prove a provider response, successful work or acceptance. Physical-call reservations are linked by dispatch ID in the execution audit.',
    'この試行の変更不可の選択記録です。プロバイダーの応答、作業の成功、受入を証明しません。実際の呼び出し予約は、実行監査の選択IDで対応付けられます。',
    '这是为此尝试记录的不可变选择。它不证明提供商已响应、工作成功或验收通过。实际调用预留在执行审计中通过调度 ID 关联。',
    '這是為此次嘗試記錄的不可變選擇。它不證明提供者已回應、工作成功或驗收通過。實際呼叫保留在執行稽核中透過派送 ID 關聯。',
    'اختيار ثابت مسجّل لهذه المحاولة. لا يثبت استجابة المزوّد أو نجاح العمل أو قبوله. ترتبط حجوزات الاستدعاءات الفعلية بمعرّف التنفيذ في سجل التدقيق.',
    'Неизменяемая запись выбора для этой попытки. Она не доказывает ответ провайдера, успех работы или приёмку. Резервации фактических вызовов связаны с ID назначения в аудите выполнения.'
  ],
  route: ['Route ID', 'ルートID', '路由 ID', '路由 ID', 'معرّف المسار', 'ID маршрута'],
  dispatchId: ['Dispatch ID', '選択ID', '调度 ID', '派送 ID', 'معرّف التنفيذ', 'ID назначения'],
  selectedProvider: ['Selected provider', '選択されたプロバイダー', '所选提供商', '所選提供者', 'المزوّد المحدد', 'Выбранный провайдер'],
  adapterProvider: ['Resolved transport provider', '解決済み通信プロバイダー', '已解析的传输提供商', '已解析的傳輸提供者', 'مزوّد النقل المحسوم', 'Разрешённый провайдер транспорта'],
  reasonFixed: ['Owner-selected Fixed route', '所有者が選んだ固定ルート', '负责人选择的固定路由', '負責人選擇的固定路由', 'المسار الثابت الذي اختاره المالك', 'Фиксированный маршрут, выбранный владельцем'],
  reasonFallback: ['Explicit Fixed fallback after an invalid complete response', '無効な完了応答後の明示的な固定フォールバック', '完整响应无效后的明确固定回退', '完整回應無效後的明確固定備援', 'بديل ثابت صريح بعد استجابة مكتملة غير صالحة', 'Явный фиксированный резерв после некорректного завершённого ответа'],
  reasonAuto: ['Cheapest eligible configured cost ceiling', '適格な設定料金上限のうち最安', '符合条件的最低配置费用上限', '符合條件的最低設定費用上限', 'أقل سقف تكلفة مضبوط مؤهل', 'Самая низкая допустимая заданная оценка стоимости'],
  selectionReason: ['Selection reason', '選択理由', '选择原因', '選擇原因', 'سبب الاختيار', 'Причина выбора'],
  pricing: ['Captured input / output price (USD per million tokens)', '保存済み入力・出力単価（100万トークンあたりUSD）', '保存的输入 / 输出价格（每百万令牌美元）', '儲存的輸入 / 輸出價格（每百萬權杖美元）', 'سعر الإدخال / الإخراج المحفوظ (دولار لكل مليون رمز)', 'Сохранённая цена входа / вывода (USD за миллион токенов)'],
  attempt: ['Attempt token', '試行トークン', '尝试令牌', '嘗試權杖', 'رمز المحاولة', 'Токен попытки'],
  legacyCall: ['Legacy reservation; no dispatch link recorded', '従来の予約・選択へのリンクは未記録', '旧版预留；未记录调度关联', '舊版保留；未記錄派送關聯', 'حجز سابق؛ لم يُسجّل رابط تنفيذ', 'Прежняя резервация; связь с назначением не записана'],
  stale: [
    'Showing the last recorded configuration. Reconnect and refresh before using its generation for a change.',
    '最後に記録した設定を表示しています。変更に世代を使う前に再接続して更新してください。',
    '正在显示最后记录的配置。使用其代次进行更改前，请重新连接并刷新。',
    '正在顯示最後記錄的設定。使用其代次進行變更前，請重新連線並重新整理。',
    'تُعرض آخر إعدادات مسجّلة. أعد الاتصال وحدّث البيانات قبل استخدام جيلها لإجراء تغيير.',
    'Показана последняя записанная настройка. Перед изменением с этим поколением восстановите подключение и обновите данные.'
  ],
  invalid: [
    'The runtime returned invalid model-routing details. Refresh before reviewing assignments or dispatches.',
    'ランタイムから無効なモデルルーティング情報が返されました。割り当てや選択記録を確認する前に更新してください。',
    '运行时返回了无效的模型路由详情。请刷新后再查看分配或调度记录。',
    '執行環境傳回無效的模型路由詳細資料。請重新整理後再查看指派或派送記錄。',
    'أعادت بيئة التشغيل تفاصيل غير صالحة لتوجيه النماذج. حدّث البيانات قبل مراجعة التعيينات أو التنفيذات.',
    'Среда выполнения вернула некорректные сведения о маршрутизации моделей. Обновите данные перед просмотром назначений.'
  ]
} as const

export type ModelRoutingCopy = { [K in keyof typeof messages]: string }

const forLocale = (index: number): ModelRoutingCopy =>
  Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as ModelRoutingCopy

export const modelRoutingCopy: Record<Locale, ModelRoutingCopy> = {
  en: forLocale(0), ja: forLocale(1), zh: forLocale(2), 'zh-hant': forLocale(3), ar: forLocale(4), ru: forLocale(5)
}
