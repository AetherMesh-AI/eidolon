import type { organizationHomeEn } from './organization-home'

type HomeCounts = Pick<typeof organizationHomeEn.detail, 'moreTasks' | 'moreRequests' | 'delegationCount' | 'ownerRequestCount'>

// Display values are already localized. Never parse them to choose a grammatical form.
export const organizationHomeCountsJa = {
  moreTasks: (_count, formatted) => `作業の全体表示に、ほか ${formatted} 件のタスクがあります`,
  moreRequests: (_count, formatted) => `下に、ほか ${formatted} 件のオーナーへの依頼があります`,
  delegationCount: (_count, formatted) => `現在のラウンドのマネージャーへの割り当て：${formatted} 件`,
  ownerRequestCount: (_count, formatted) => `対応待ちのオーナーへの依頼：${formatted} 件`
} satisfies HomeCounts

export const organizationHomeCountsZh = {
  moreTasks: (_count, formatted) => `完整工作视图中还有 ${formatted} 项任务`,
  moreRequests: (_count, formatted) => `下方还有 ${formatted} 项面向所有者的请求`,
  delegationCount: (_count, formatted) => `本轮经理任务分配：${formatted} 项`,
  ownerRequestCount: (_count, formatted) => `待处理的所有者请求：${formatted} 项`
} satisfies HomeCounts

export const organizationHomeCountsZhHant = {
  moreTasks: (_count, formatted) => `完整工作檢視中還有 ${formatted} 項任務`,
  moreRequests: (_count, formatted) => `下方還有 ${formatted} 項向擁有者提出的請求`,
  delegationCount: (_count, formatted) => `本輪經理任務指派：${formatted} 項`,
  ownerRequestCount: (_count, formatted) => `待處理的擁有者請求：${formatted} 項`
} satisfies HomeCounts

// Count labels avoid requiring a numeral to agree with an Arabic dual noun.
export const organizationHomeCountsAr = {
  moreTasks: (_count, formatted) => `مهام إضافية في عرض العمل الكامل: ${formatted}`,
  moreRequests: (_count, formatted) => `طلبات إضافية للمالك أدناه: ${formatted}`,
  delegationCount: (_count, formatted) => `تكليفات المديرين في الجولة الحالية: ${formatted}`,
  ownerRequestCount: (_count, formatted) => `طلبات المالك المعلقة: ${formatted}`
} satisfies HomeCounts

const russianPlurals = new Intl.PluralRules('ru')
function russianForm(count: number, one: string, few: string, many: string) {
  const category = russianPlurals.select(count)
  return category === 'one' ? one : category === 'few' ? few : many
}

export const organizationHomeCountsRu = {
  moreTasks: (count, formatted) => `Ещё ${formatted} ${russianForm(count, 'задача', 'задачи', 'задач')} в полном списке работ`,
  moreRequests: (count, formatted) => `Ещё ${formatted} ${russianForm(count, 'запрос', 'запроса', 'запросов')} владельцу ниже`,
  delegationCount: (count, formatted) => `${formatted} ${russianForm(count, 'назначение', 'назначения', 'назначений')} менеджерам в текущем раунде`,
  ownerRequestCount: (count, formatted) => `${formatted} ${russianForm(count, 'ожидающий запрос', 'ожидающих запроса', 'ожидающих запросов')} владельцу`
} satisfies HomeCounts
