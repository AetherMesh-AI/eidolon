import type { Locale } from './types'

const messages = {
  heading: [
    'Completion reserve',
    '完了用の確保枠',
    '完成预留额度',
    '完成保留額度',
    'حصة إتمام العمل',
    'Резерв на завершение'
  ],
  totalRemaining: [
    'Total remaining allowance',
    '残りの利用枠（合計）',
    '剩余总额度',
    '剩餘總額度',
    'إجمالي الحصة المتبقية',
    'Общий остаток лимита'
  ],
  ordinaryRemaining: [
    'Remaining for ordinary work',
    '通常作業に使える残りの枠',
    '常规工作剩余额度',
    '一般工作剩餘額度',
    'المتبقي للعمل العادي',
    'Остаток для обычной работы'
  ],
  protectedRemaining: [
    'Unspent completion protection',
    '完了用の未使用確保枠',
    '尚未使用的完成保护额度',
    '尚未使用的完成保護額度',
    'الحصة المحمية غير المستخدمة للإتمام',
    'Неиспользованный резерв на завершение'
  ],
  completionReserved: [
    'Completion calls already reserved',
    '完了処理で予約済みの枠',
    '完成调用已预留额度',
    '完成呼叫已保留額度',
    'الحصة المحجوزة بالفعل لاستدعاءات الإتمام',
    'Уже зарезервировано вызовами завершения'
  ],
  captured: [
    'Owner-configured completion reserve',
    '所有者が設定した完了用の確保枠',
    '负责人配置的完成预留额度',
    '負責人設定的完成保留額度',
    'حصة الإتمام التي ضبطها المالك',
    'Резерв на завершение, заданный владельцем'
  ],
  modelCalls: ['Model calls', 'モデル呼び出し', '模型调用', '模型呼叫', 'استدعاءات النموذج', 'Вызовы модели'],
  tokens: ['Reserved tokens', '予約トークン', '预留令牌', '保留權杖', 'الرموز المحجوزة', 'Резервируемые токены'],
  configuredCostUsd: [
    'Configured USD',
    '設定料金（USD）',
    '配置费用（USD）',
    '設定費用（USD）',
    'التكلفة المحددة بالدولار',
    'Заданные доллары США'
  ],
  unknown: ['Unknown', '不明', '未知', '未知', 'غير معروف', 'Неизвестно'],
  notConfigured: ['Not configured', '未設定', '未配置', '未設定', 'غير مضبوط', 'Не задано'],
  note: [
    'Read-only policy captured from the owner’s config.yaml. Ordinary work preserves this allowance for task review, test review, integration and final acceptance. Completion calls can also use ordinary allowance; reservations are never refunded.',
    '所有者のconfig.yamlから保存した読み取り専用の方針です。通常作業はタスクレビュー、テストレビュー、統合、最終受入のためにこの枠を残します。完了処理は通常枠も使用でき、予約済みの枠は返却されません。',
    '这是从负责人 config.yaml 保存的只读策略。常规工作为任务审查、测试审查、整合和最终验收保留此额度。完成调用也可使用常规额度；已预留额度不会退还。',
    '這是從負責人 config.yaml 儲存的唯讀策略。一般工作為任務審查、測試審查、整合及最終驗收保留此額度。完成呼叫也可使用一般額度；已保留額度不會退還。',
    'سياسة للقراءة فقط محفوظة من ملف config.yaml الخاص بالمالك. يحافظ العمل العادي على هذه الحصة لمراجعة المهام والاختبارات والدمج والقبول النهائي. يمكن لاستدعاءات الإتمام أيضاً استخدام الحصة العادية، ولا تُسترد الحجوزات.',
    'Политика только для чтения, сохранённая из config.yaml владельца. Обычная работа оставляет этот резерв для проверки задач и тестов, интеграции и окончательной приёмки. Вызовы завершения также могут использовать обычный лимит; резервации не возвращаются.'
  ],
  boundary: [
    'This controls admission of physical model calls for this objective only. Current hard caps, stage limits and the deadline still apply. It is not a subscription limit or a tool/provider bill, and does not guarantee completion.',
    'この目標の実際のモデル呼び出しの許可だけを制御します。現在の上限、段階数制限、期限は引き続き適用されます。サブスクリプションの上限やツール・プロバイダーの請求額ではなく、完了を保証しません。',
    '此策略仅控制该目标实际模型调用的准入。当前硬上限、阶段限制和截止时间仍然适用。这不是订阅上限或工具及提供商账单，也不保证完成。',
    '此策略僅控制該目標實際模型呼叫的准入。目前硬上限、階段限制及截止時間仍然適用。這不是訂閱上限或工具及提供者帳單，也不保證完成。',
    'تتحكم هذه السياسة فقط في السماح باستدعاءات النموذج الفعلية لهذا الهدف. تظل الحدود القصوى الحالية وحدود المراحل والموعد النهائي سارية. وهي ليست حداً للاشتراك أو فاتورة للأدوات أو المزوّد، ولا تضمن الإتمام.',
    'Это только допуск фактических вызовов модели для данной цели. Текущие жёсткие лимиты, предел этапов и срок сохраняются. Это не лимит подписки и не счёт за инструменты или провайдера; завершение не гарантируется.'
  ],
  shortfall: [
    'Current hard caps leave less allowance than the unspent completion protection. The protection does not add capacity.',
    '現在の上限による残りの枠は、完了用の未使用確保枠より少なくなっています。確保枠が利用可能量を増やすことはありません。',
    '当前硬上限下的剩余额度低于尚未使用的完成保护额度。保护额度不会增加可用容量。',
    '目前硬上限下的剩餘額度低於尚未使用的完成保護額度。保護額度不會增加可用容量。',
    'الحصة المتبقية تحت الحدود القصوى الحالية أقل من حصة الإتمام المحمية غير المستخدمة. الحماية لا تضيف سعة.',
    'Текущие жёсткие лимиты оставляют меньше, чем неиспользованный резерв на завершение. Резерв не добавляет доступный лимит.'
  ],
  unavailable: [
    'Some remaining allowance is unknown because prior usage, configured cost or model-call classification is unknown. Unknown is not zero or permission for another call.',
    '過去の使用量、設定料金、またはモデル呼び出しの分類が不明なため、残りの枠の一部は不明です。不明はゼロや追加呼び出しの許可を意味しません。',
    '由于早期用量、配置费用或模型调用分类未知，部分剩余额度未知。未知不代表零，也不表示允许再次调用。',
    '由於早期用量、設定費用或模型呼叫分類未知，部分剩餘額度未知。未知不代表零，也不表示允許再次呼叫。',
    'بعض الحصة المتبقية غير معروف لأن الاستخدام السابق أو التكلفة المحددة أو تصنيف استدعاءات النموذج غير معروف. غير المعروف لا يعني صفراً أو السماح باستدعاء آخر.',
    'Часть остатка неизвестна, поскольку неизвестны прошлое использование, заданная стоимость или классификация вызовов модели. Неизвестное не означает ноль или разрешение на новый вызов.'
  ],
  invalid: [
    'The runtime returned invalid completion-reserve details. Refresh before reviewing the allowance.',
    'ランタイムから無効な完了確保枠が返されました。利用枠を確認する前に更新してください。',
    '运行时返回了无效的完成预留详情。请刷新后再查看额度。',
    '執行環境傳回無效的完成保留詳細資料。請重新整理後再查看額度。',
    'أعادت بيئة التشغيل تفاصيل غير صالحة لحصة الإتمام. حدّث البيانات قبل مراجعة الحصة.',
    'Среда выполнения вернула некорректные сведения о резерве на завершение. Обновите данные перед просмотром лимита.'
  ],
  incompleteTokens: [
    'Observed token lower bounds (input / output; totals unknown)',
    '確認済みトークン数の下限（入力 / 出力・合計は不明）',
    '已观测令牌下限（输入 / 输出；总量未知）',
    '已觀測權杖下限（輸入 / 輸出；總量未知）',
    'الحد الأدنى للرموز المرصودة (إدخال / إخراج؛ الإجماليات غير معروفة)',
    'Нижние границы наблюдаемых токенов (вход / выход; итоги неизвестны)'
  ]
} as const

type CompletionReserveCopy = { [K in keyof typeof messages]: string }

const forLocale = (index: number): CompletionReserveCopy =>
  Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as CompletionReserveCopy

export const completionReserveCopy: Record<Locale, CompletionReserveCopy> = {
  en: forLocale(0),
  ja: forLocale(1),
  zh: forLocale(2),
  'zh-hant': forLocale(3),
  ar: forLocale(4),
  ru: forLocale(5)
}
