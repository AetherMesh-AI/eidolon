import type { OrganizationRosterCopy } from './organization-roster'

type IdentityCopy = Pick<OrganizationRosterCopy,
  'executive' | 'manager' | 'worker' | 'owner' | 'persistentIdentity' | 'identityId' | 'createdAt' |
  'purpose' | 'contextSummary' | 'memory' | 'facts' | 'decisions' | 'lessons' | 'openQuestions' |
  'noMemory' | 'contextUnavailable' | 'memoryNote' | 'revision' | 'updatedAt' | 'history' |
  'historyNote' | 'noHistory' | 'evidenceIds' | 'taskId' | 'requestType' | 'managingAgent' |
  'managementAssignment' | 'plannedAssignment'>

export const organizationIdentityAr: IdentityCopy = {
  executive: 'مسؤول تنفيذي', manager: 'مدير', worker: 'عامل', owner: 'المالك',
  persistentIdentity: 'هوية دائمة', identityId: 'معرّف الهوية', createdAt: 'تاريخ إنشاء الهوية',
  purpose: 'الغرض', contextSummary: 'ملخّص السياق', memory: 'الذاكرة الشخصية',
  facts: 'الحقائق', decisions: 'القرارات', lessons: 'الدروس المستفادة', openQuestions: 'الأسئلة المفتوحة',
  noMemory: 'لم يُسجّل شيء بعد.',
  contextUnavailable: 'لم تُبلّغ بيئة التشغيل عن سياق محفوظ لهذه الهوية.',
  memoryNote: 'ذاكرة محدودة تُحفظ لهذه الهوية عبر التكليفات. لا تمنح هذه الملخّصات صلاحيات ولا تحلّ محل أدلة المهام الدقيقة.',
  revision: 'نسخة الذاكرة', updatedAt: 'آخر تحديث للسياق', history: 'سجل الهوية الحديث',
  historyNote: 'ملخّصات محفوظة حديثاً، الأحدث أولاً. تبقى الأدلة الكاملة في سجل المؤسسة.',
  noHistory: 'لم يُسجّل عمل مكتمل لهذه الهوية بعد.',
  evidenceIds: 'معرّفات الأدلة', taskId: 'معرّف المهمة', requestType: 'نوع الطلب',
  managingAgent: 'المدير ضمن النطاق', managementAssignment: 'التكليف الذي يديره', plannedAssignment: 'التكليف المخطّط'
}

export const organizationIdentityRu: IdentityCopy = {
  executive: 'Руководитель', manager: 'Менеджер', worker: 'Исполнитель', owner: 'Владелец',
  persistentIdentity: 'Постоянная идентичность', identityId: 'Идентификатор', createdAt: 'Дата создания',
  purpose: 'Назначение', contextSummary: 'Сводка контекста', memory: 'Личная память',
  facts: 'Факты', decisions: 'Решения', lessons: 'Усвоенные уроки', openQuestions: 'Открытые вопросы',
  noMemory: 'Пока ничего не записано.',
  contextUnavailable: 'Среда выполнения не предоставила сохранённый контекст этого участника.',
  memoryNote: 'Ограниченная память сохраняется за участником между заданиями. Эти сводки не дают разрешений и не заменяют точные доказательства по задачам.',
  revision: 'Версия памяти', updatedAt: 'Последнее обновление контекста', history: 'Последняя история участника',
  historyNote: 'Недавние сохранённые сводки, начиная с новых. Полные доказательства остаются в журнале организации.',
  noHistory: 'Для этого участника пока не записана завершённая работа.',
  evidenceIds: 'Идентификаторы доказательств', taskId: 'Идентификатор задачи', requestType: 'Тип запроса',
  managingAgent: 'Менеджер в рамках задания', managementAssignment: 'Управляемое задание', plannedAssignment: 'Запланированное задание'
}
