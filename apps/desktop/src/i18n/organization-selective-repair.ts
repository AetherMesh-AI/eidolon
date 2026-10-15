import type { Locale } from './types'

export const selectiveRepairCopy: Record<Locale, { reused: string; detail: string; invalid: string; plan: string; planDetail: string }> = {
  en: { plan: 'Reused manager plan', planDetail: 'Original planning request retained. No new planning call.', reused: 'Reused reviewed output', detail: 'Retained exact output and review from an earlier round. No new execution.', invalid: 'Invalid retained evidence record.' },
  ja: { plan: '管理者の計画を再利用', planDetail: '元の計画リクエストを保持しています。新しい計画呼び出しはありません。', reused: 'レビュー済み出力を再利用', detail: '以前のラウンドの出力とレビューをそのまま保持しています。新しい実行ではありません。', invalid: '保持された証拠の記録が無効です。' },
  zh: { plan: '复用管理者计划', planDetail: '保留原始规划请求，未发起新的规划调用。', reused: '复用已审核的输出', detail: '保留先前轮次的原始输出和审核，未重新执行。', invalid: '保留的证据记录无效。' },
  'zh-hant': { plan: '重用管理者計畫', planDetail: '保留原始規劃請求，未發起新的規劃呼叫。', reused: '重用已審核的輸出', detail: '保留先前回合的原始輸出和審核，未重新執行。', invalid: '保留的證據記錄無效。' },
  ar: { plan: 'إعادة استخدام خطة المدير', planDetail: 'تم الاحتفاظ بطلب التخطيط الأصلي. لا يوجد استدعاء تخطيط جديد.', reused: 'إعادة استخدام مخرج تمت مراجعته', detail: 'تم الاحتفاظ بالمخرج الدقيق ومراجعته من دورة سابقة. لم يحدث تنفيذ جديد.', invalid: 'سجل الدليل المحتفظ به غير صالح.' },
  ru: { plan: 'Повторно использован план менеджера', planDetail: 'Сохранён исходный запрос планирования. Нового вызова не было.', reused: 'Повторно использован проверенный результат', detail: 'Сохранены точный результат и его проверка из предыдущего цикла. Нового выполнения не было.', invalid: 'Недопустимая запись сохранённого доказательства.' }
}
