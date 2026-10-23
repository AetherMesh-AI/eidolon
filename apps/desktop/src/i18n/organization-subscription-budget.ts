import type { Locale } from './types'

export const subscriptionBudgetCopy: Record<Locale, { support: string; limitation: string; reported: string }> = {
  en: {
    support: 'OpenAI API and Codex subscription routes use this organization’s own connection.',
    limitation:
      'Subscription output has an admission reservation, not a guaranteed provider-enforced token ceiling. Call and input limits, pacing, deadlines and cancellation remain enforced. Reported usage may exceed reservations; missing usage is unknown. A configured monetary ceiling cannot authorize this route.',
    reported: 'Subscription attempts: reported input / output tokens'
  },
  ja: {
    support: 'OpenAI API と Codex サブスクリプションは、この組織専用の接続を使用します。',
    limitation:
      'サブスクリプションの出力は受付時に予約されますが、プロバイダーが保証するトークン上限ではありません。呼び出し・入力の上限、実行間隔、期限、キャンセルは引き続き適用されます。報告使用量は予約を超える場合があり、未報告分は不明です。金額上限を設定した場合、この接続は許可されません。',
    reported: 'サブスクリプションの試行：報告された入力 / 出力トークン'
  },
  zh: {
    support: 'OpenAI API 和 Codex 订阅使用此组织自己的连接。',
    limitation:
      '订阅输出仅在准入时预留，不保证服务商强制执行令牌上限。调用与输入限制、节奏、截止时间和取消仍然生效。报告用量可能超过预留；未报告用量未知。设置金额上限后不能授权此路由。',
    reported: '订阅尝试：报告的输入 / 输出令牌'
  },
  'zh-hant': {
    support: 'OpenAI API 和 Codex 訂閱使用此組織自己的連線。',
    limitation:
      '訂閱輸出僅於准入時保留，不保證服務商強制執行權杖上限。呼叫與輸入限制、節奏、截止時間和取消仍然生效。回報用量可能超過保留；未回報用量未知。設定金額上限後不能授權此路由。',
    reported: '訂閱嘗試：回報的輸入 / 輸出權杖'
  },
  ar: {
    support: 'تستخدم مسارات OpenAI API واشتراك Codex اتصال هذه المؤسسة فقط.',
    limitation:
      'يُحجز إخراج الاشتراك عند قبول الطلب، دون سقف رموز مضمون يفرضه المزوّد. تبقى حدود الاستدعاء والإدخال والوتيرة والمواعيد النهائية والإلغاء سارية. قد يتجاوز الاستخدام المبلّغ الحجز؛ والاستخدام غير المبلّغ مجهول. لا يسمح السقف المالي المحدد بهذا المسار.',
    reported: 'محاولات الاشتراك: رموز الإدخال / الإخراج المبلّغة'
  },
  ru: {
    support: 'Маршруты OpenAI API и подписки Codex используют подключение этой организации.',
    limitation:
      'Для вывода подписки резервируется объём при допуске, но провайдер не гарантирует верхний предел токенов. Ограничения вызовов и ввода, темп, сроки и отмена сохраняются. Заявленное использование может превышать резерв; неизвестные данные остаются неизвестными. Заданный денежный лимит не разрешает этот маршрут.',
    reported: 'Попытки подписки: заявленные токены ввода / вывода'
  }
}
