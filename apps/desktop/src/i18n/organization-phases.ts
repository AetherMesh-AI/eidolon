/** Only typed runtime phase codes are translated; retained free text is not matched. */
const phases = {
  legacy_reviewed: ['Legacy reviewed outcome', '旧方式のレビュー済み成果', '旧版已审查成果', '舊版已審查成果', 'نتيجة تمت مراجعتها بالنظام السابق', 'Результат, проверенный по прежней схеме'],
  accepted: ['Accepted integrated outcome', '統合成果を受け入れ済み', '已接受的整合成果', '已接受的整合成果', 'نتيجة متكاملة مقبولة', 'Принятый итоговый результат'],
  integration_review: ['Integrated outcome acceptance', '統合成果の受け入れ審査', '整合成果验收', '整合成果驗收', 'التحقق من قبول النتيجة المتكاملة', 'Приёмка итогового результата'],
  intervention: ['Pending intervention', '対応待ち', '等待干预', '等待介入', 'بانتظار التدخل', 'Ожидает вмешательства'],
  executive_planning: ['Executive decomposition and manager planning', 'エグゼクティブによる分解とマネージャーによる計画', '主管分解与经理规划', '主管分解與經理規劃', 'تقسيم الهدف بواسطة المسؤول التنفيذي وتخطيط المدير', 'Декомпозиция руководителем и планирование менеджером'],
  legacy_planning: ['Legacy manager planning', '旧方式のマネージャー計画', '旧版经理规划', '舊版經理規劃', 'تخطيط المدير بالنظام السابق', 'Планирование менеджером по прежней схеме'],
  execution_review: ['Execution and review', '実行とレビュー', '执行与审查', '執行與審查', 'التنفيذ والمراجعة', 'Выполнение и проверка']
} as const

export type OrganizationPhaseCopy = { [K in keyof typeof phases]: string }
const forLocale = (index: number): OrganizationPhaseCopy => Object.fromEntries(Object.entries(phases).map(([key, values]) => [key, values[index]])) as OrganizationPhaseCopy
export const organizationPhasesEn = forLocale(0)
export const organizationPhasesJa = forLocale(1)
export const organizationPhasesZh = forLocale(2)
export const organizationPhasesZhHant = forLocale(3)
export const organizationPhasesAr = forLocale(4)
export const organizationPhasesRu = forLocale(5)
