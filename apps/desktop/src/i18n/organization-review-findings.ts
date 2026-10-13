/** Labels only: criterion text, judgments and conflicts remain backend records. */
const messages = {
  heading: ['Recorded review findings', '記録されたレビュー所見', '已记录的审查结论', '已記錄的審查結論', 'نتائج المراجعة المسجلة', 'Записанные выводы проверки'],
  note: ['These criterion judgments are separate from final acceptance.', '基準ごとの判断と最終的な受け入れは別の記録です。', '逐项标准判断与最终验收是不同的记录。', '逐項標準判斷與最終驗收是不同的紀錄。', 'هذه الأحكام على المعايير منفصلة عن القبول النهائي.', 'Оценки критериев не означают окончательной приёмки.'],
  unmetCriteria: ['Unmet criteria', '未充足の基準', '未满足的标准', '未滿足的標準', 'معايير غير مستوفاة', 'Невыполненные критерии'],
  criteria: ['Criterion judgments', '基準ごとの判断', '逐项标准判断', '逐項標準判斷', 'الأحكام على المعايير', 'Оценки критериев'],
  conflicts: ['Unresolved conflicts', '未解決の矛盾', '未解决的冲突', '未解決的衝突', 'تعارضات لم تُحلّ', 'Неразрешённые противоречия'],
  satisfied: ['Met', '充足', '已满足', '已滿足', 'مستوفى', 'Выполнен'],
  unsatisfied: ['Not met', '未充足', '未满足', '未滿足', 'غير مستوفى', 'Не выполнен'],
  evidence: ['Supporting evidence', '根拠となる証拠', '支持证据', '支持證據', 'الأدلة الداعمة', 'Подтверждающие доказательства']
} as const

type ReviewFindingsCopy = { [K in keyof typeof messages]: string }
const forLocale = (index: number): ReviewFindingsCopy => Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as ReviewFindingsCopy
export const reviewFindingsEn = forLocale(0)
export const reviewFindingsJa = forLocale(1)
export const reviewFindingsZh = forLocale(2)
export const reviewFindingsZhHant = forLocale(3)
export const reviewFindingsAr = forLocale(4)
export const reviewFindingsRu = forLocale(5)
