import type { OrganizationHomeCopy } from './organization-home'
import { organizationPhasesAr, organizationPhasesJa, organizationPhasesRu, organizationPhasesZh, organizationPhasesZhHant } from './organization-phases'
import { reviewFindingsAr, reviewFindingsJa, reviewFindingsRu, reviewFindingsZh, reviewFindingsZhHant } from './organization-review-findings'

type DetailCopy = Omit<OrganizationHomeCopy['detail'], 'moreTasks' | 'moreRequests' | 'delegationCount' | 'ownerRequestCount'>

// Translate presentation and explicit runtime phase codes; free text stays authored.
export const organizationObjectiveDetailJa: DetailCopy = {
  phases: organizationPhasesJa,
  reviewFindings: reviewFindingsJa,
  tasksLabel: "タスク",
  resultLabel: "結果",
  noReviewedResult: "レビュー済みの結果はまだありません。",
  runtimeNote: "状態と完了は現在のプロファイルの実行環境から報告されます。",
  fullEvidence: "タスクの証拠全文",
  views: "目標の表示",
  overview: '目標の概要', progress: '進捗の概要', progressNote: '記録されたタスクの進捗です。最終的な受け入れは別途判断されます。',
  phase: '現在の段階', phaseUnknown: '段階は未報告', taskStates: '記録されたタスクの状態',
  taskStatus: { completed: '完了', working: '作業中', review: 'レビュー中', blocked: 'ブロック中', queued: '待機中', cancelled: 'キャンセル済み', unknown: '状態不明' },
  noTasks: 'この表示に現在のタスクは記録されていません。', teamWork: '委任された作業',
  teamNote: '現在のラウンドのマネージャーへの割り当てと記録されたタスクです。以前のラウンドは作業履歴に残ります。',
  packageStatus: '割り当ての状態', otherTasks: 'その他の記録されたタスク',
  otherTasksNote: 'この表示では、これらのタスクに現在のラウンドのマネージャー割り当てが関連付けられていません。',
  dependenciesWaiting: '記録された依存関係を待機中', attentionAndDelivery: '所有者の入力と成果物', needsInput: '入力が必要です',
  delivery: '成果物', notAccepted: '成果物の最終的な受け入れは記録されていません。', acceptedRecorded: '受け入れ済みの成果物はレビューの証拠で確認できます。',
  inspectWork: '作業と証拠を確認', inspectRequests: '所有者への依頼を確認', inspectAcceptance: '受け入れと成果物を確認',
  objectiveRuntime: '目標 · 実行環境', inspectObjective: '目標を確認', objectiveControls: '目標の操作', requestControls: '目標への依頼の操作', workDetails: '目標の作業詳細'
}

export const organizationObjectiveDetailZh: DetailCopy = {
  phases: organizationPhasesZh,
  reviewFindings: reviewFindingsZh,
  tasksLabel: "任务",
  resultLabel: "结果",
  noReviewedResult: "暂无已审核的结果。",
  runtimeNote: "状态和完成情况由当前配置文件的运行环境报告。",
  fullEvidence: "完整任务证据",
  views: "目标视图",
  overview: '目标概览', progress: '进度概览', progressNote: '已记录的任务进度。最终验收需单独决定。',
  phase: '当前阶段', phaseUnknown: '尚未报告阶段', taskStates: '已记录的任务状态',
  taskStatus: { completed: '已完成', working: '工作中', review: '审核中', blocked: '受阻', queued: '排队中', cancelled: '已取消', unknown: '未知状态' },
  noTasks: '此视图中未记录当前任务。', teamWork: '委派的工作',
  teamNote: '本轮经理工作分配及其已记录的任务。之前的轮次保留在工作历史中。',
  packageStatus: '分配状态', otherTasks: '其他已记录的任务', otherTasksNote: '此视图中，这些任务未关联本轮的经理工作分配。',
  dependenciesWaiting: '等待已记录的依赖项', attentionAndDelivery: '所有者输入与交付', needsInput: '需要您的输入',
  delivery: '交付', notAccepted: '尚未记录包含交付物的最终验收。', acceptedRecorded: '可在审核证据中查看已验收的交付物。',
  inspectWork: '检查工作与证据', inspectRequests: '查看所有者请求', inspectAcceptance: '检查验收与交付',
  objectiveRuntime: '目标 · 运行环境', inspectObjective: '检查目标', objectiveControls: '目标控件', requestControls: '目标请求控件', workDetails: '目标工作详情'
}

export const organizationObjectiveDetailZhHant: DetailCopy = {
  phases: organizationPhasesZhHant,
  reviewFindings: reviewFindingsZhHant,
  tasksLabel: "任務",
  resultLabel: "結果",
  noReviewedResult: "尚無已審查的結果。",
  runtimeNote: "狀態和完成情況由目前設定檔的執行環境回報。",
  fullEvidence: "完整任務證據",
  views: "目標檢視",
  overview: '目標概覽', progress: '進度概覽', progressNote: '已記錄的任務進度。最終驗收需另行決定。',
  phase: '目前階段', phaseUnknown: '尚未回報階段', taskStates: '已記錄的任務狀態',
  taskStatus: { completed: '已完成', working: '工作中', review: '審查中', blocked: '受阻', queued: '排隊中', cancelled: '已取消', unknown: '未知狀態' },
  noTasks: '此檢視中未記錄目前的任務。', teamWork: '委派的工作',
  teamNote: '本輪經理工作指派及其已記錄的任務。先前的輪次保留在工作歷史中。',
  packageStatus: '指派狀態', otherTasks: '其他已記錄的任務', otherTasksNote: '此檢視中，這些任務未連結本輪的經理工作指派。',
  dependenciesWaiting: '等待已記錄的相依項目', attentionAndDelivery: '擁有者輸入與交付', needsInput: '需要您的輸入',
  delivery: '交付', notAccepted: '尚未記錄包含交付項目的最終驗收。', acceptedRecorded: '可在審查證據中檢視已驗收的交付項目。',
  inspectWork: '檢查工作與證據', inspectRequests: '檢視擁有者請求', inspectAcceptance: '檢查驗收與交付',
  objectiveRuntime: '目標 · 執行環境', inspectObjective: '檢查目標', objectiveControls: '目標控制項', requestControls: '目標請求控制項', workDetails: '目標工作詳情'
}

export const organizationObjectiveDetailAr: DetailCopy = {
  phases: organizationPhasesAr,
  reviewFindings: reviewFindingsAr,
  tasksLabel: "المهام",
  resultLabel: "النتيجة",
  noReviewedResult: "لا توجد نتيجة تمت مراجعتها بعد.",
  runtimeNote: "تُبلّغ بيئة تشغيل ملف التعريف الحالي الحالة والاكتمال.",
  fullEvidence: "أدلة المهمة الكاملة",
  views: "عروض الهدف",
  overview: 'نظرة عامة على الهدف', progress: 'نظرة عامة على التقدم', progressNote: 'تقدم المهام المسجل. القبول النهائي قرار منفصل.',
  phase: 'المرحلة الحالية', phaseUnknown: 'لم تُبلّغ المرحلة', taskStates: 'حالات المهام المسجلة',
  taskStatus: { completed: 'مكتملة', working: 'قيد العمل', review: 'قيد المراجعة', blocked: 'متعطلة', queued: 'في الانتظار', cancelled: 'ملغاة', unknown: 'حالة غير معروفة' },
  noTasks: 'لا توجد مهام حالية مسجلة في هذا العرض.', teamWork: 'العمل المفوّض',
  teamNote: 'تكليفات المديرين في الجولة الحالية ومهامهم المسجلة. تبقى الجولات السابقة في سجل العمل.',
  packageStatus: 'حالة التكليف', otherTasks: 'مهام مسجلة أخرى', otherTasksNote: 'لا ترتبط هذه المهام بتكليف مدير في الجولة الحالية ضمن هذا العرض.',
  dependenciesWaiting: 'في انتظار الاعتماديات المسجلة', attentionAndDelivery: 'مدخلات المالك والتسليم', needsInput: 'مطلوب إدخالك',
  delivery: 'التسليم', notAccepted: 'لم يُسجّل قبول نهائي مع مخرج قابل للتسليم.', acceptedRecorded: 'المخرج المقبول متاح ضمن أدلة المراجعة.',
  inspectWork: 'فحص العمل والأدلة', inspectRequests: 'مراجعة طلبات المالك', inspectAcceptance: 'فحص القبول والتسليم',
  objectiveRuntime: 'الهدف · بيئة التشغيل', inspectObjective: 'فحص الهدف', objectiveControls: 'عناصر تحكم الهدف', requestControls: 'عناصر تحكم طلبات الهدف', workDetails: 'تفاصيل عمل الهدف'
}

export const organizationObjectiveDetailRu: DetailCopy = {
  phases: organizationPhasesRu,
  reviewFindings: reviewFindingsRu,
  tasksLabel: "Задачи",
  resultLabel: "Результат",
  noReviewedResult: "Проверенного результата пока нет.",
  runtimeNote: "Состояние и завершение сообщает среда выполнения текущего профиля.",
  fullEvidence: "Полные доказательства задачи",
  views: "Представления цели",
  overview: 'Обзор цели', progress: 'Обзор прогресса', progressNote: 'Записанный прогресс задач. Окончательная приёмка — отдельное решение.',
  phase: 'Текущий этап', phaseUnknown: 'Этап не указан', taskStates: 'Записанные состояния задач',
  taskStatus: { completed: 'Завершено', working: 'В работе', review: 'На проверке', blocked: 'Заблокировано', queued: 'В очереди', cancelled: 'Отменено', unknown: 'Неизвестный статус' },
  noTasks: 'В этом представлении нет записанных текущих задач.', teamWork: 'Делегированная работа',
  teamNote: 'Назначения менеджерам и записанные задачи текущего раунда. Предыдущие раунды сохранены в истории работы.',
  packageStatus: 'Состояние назначения', otherTasks: 'Другие записанные задачи', otherTasksNote: 'В этом представлении задачи не связаны с назначением менеджеру в текущем раунде.',
  dependenciesWaiting: 'Ожидание записанных зависимостей', attentionAndDelivery: 'Вопросы владельцу и результат', needsInput: 'Нужен ваш ответ',
  delivery: 'Результат', notAccepted: 'Окончательная приёмка с результатом работы не записана.', acceptedRecorded: 'Принятый результат доступен в материалах проверки.',
  inspectWork: 'Проверить работу и доказательства', inspectRequests: 'Просмотреть запросы владельцу', inspectAcceptance: 'Проверить приёмку и результат',
  objectiveRuntime: 'Цель · Среда выполнения', inspectObjective: 'Просмотреть цель', objectiveControls: 'Управление целью', requestControls: 'Управление запросами цели', workDetails: 'Подробности работы над целью'
}
