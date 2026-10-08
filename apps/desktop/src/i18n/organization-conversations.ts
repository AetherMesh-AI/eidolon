/** Read-only organization conversation copy across every supported locale. */
const copy = {
  heading: [
    'Agent conversations',
    'エージェント間の会話',
    '智能体对话',
    '代理對話',
    'محادثات الوكلاء',
    'Диалоги агентов'
  ],
  note: [
    'Internal messages retained by the runtime. Inspecting them here does not mark them read by the recipient.',
    'ランタイムが保存した内部メッセージです。ここで閲覧しても受信者の既読にはなりません。',
    '运行时保留的内部消息。在此查看不会将其标记为接收方已读。',
    '執行環境保留的內部訊息。在此檢視不會將其標記為收件者已讀。',
    'رسائل داخلية محفوظة في بيئة التشغيل. عرضها هنا لا يضع علامة قرأها المستلم.',
    'Внутренние сообщения, сохранённые средой выполнения. Просмотр здесь не отмечает их прочитанными получателем.'
  ],
  empty: [
    'No agent conversations recorded',
    'エージェント間の会話は未記録です',
    '尚无智能体对话记录',
    '尚無代理對話紀錄',
    'لم تُسجّل محادثات للوكلاء',
    'Диалоги агентов ещё не записаны'
  ],
  agentEmpty: [
    'No conversations recorded for this agent',
    'このエージェントの会話は未記録です',
    '此智能体尚无对话记录',
    '此代理尚無對話紀錄',
    'لم تُسجّل محادثات لهذا الوكيل',
    'У этого агента ещё нет записанных диалогов'
  ],
  unavailable: [
    'This runtime has not reported internal conversations.',
    'このランタイムは内部会話を報告していません。',
    '此运行时尚未报告内部对话。',
    '此執行環境尚未回報內部對話。',
    'لم تُبلغ بيئة التشغيل عن محادثات داخلية.',
    'Эта среда выполнения не предоставила внутренние диалоги.'
  ],
  invalid: [
    'The runtime returned an invalid internal conversation record.',
    'ランタイムから無効な内部会話記録が返されました。',
    '运行时返回了无效的内部对话记录。',
    '執行環境傳回了無效的內部對話紀錄。',
    'أعادت بيئة التشغيل سجل محادثة داخلية غير صالح.',
    'Среда выполнения вернула некорректную запись внутреннего диалога.'
  ],
  inspect: ['Read conversation', '会話を読む', '阅读对话', '閱讀對話', 'قراءة المحادثة', 'Читать диалог'],
  more: [
    'Show more conversations',
    '会話をさらに表示',
    '显示更多对话',
    '顯示更多對話',
    'عرض المزيد من المحادثات',
    'Показать ещё диалоги'
  ],
  back: [
    'Back to conversations',
    '会話一覧に戻る',
    '返回对话列表',
    '返回對話清單',
    'العودة إلى المحادثات',
    'Назад к диалогам'
  ],
  participants: ['Participants', '参加者', '参与者', '參與者', 'المشاركون', 'Участники'],
  waiting_reply: ['Waiting for reply', '返信待ち', '等待回复', '等待回覆', 'بانتظار الرد', 'Ожидает ответа'],
  answered: ['Answered', '回答済み', '已回复', '已回覆', 'تم الرد', 'Ответ получен'],
  needs_input: ['Needs input', '入力が必要', '需要输入', '需要輸入', 'يحتاج إلى معلومات', 'Требуются данные'],
  cancelled: ['Cancelled', 'キャンセル済み', '已取消', '已取消', 'أُلغيت', 'Отменён'],
  waitingFor: [
    'Waiting on agent',
    '応答待ちのエージェント',
    '等待智能体',
    '等待代理',
    'بانتظار الوكيل',
    'Ожидается агент'
  ],
  status: ['Status', '状態', '状态', '狀態', 'الحالة', 'Статус'],
  conversationId: ['Conversation ID', '会話 ID', '对话 ID', '對話 ID', 'معرّف المحادثة', 'ID диалога'],
  objective: ['Objective', '目標', '目标', '目標', 'الهدف', 'Цель'],
  task: ['Task', 'タスク', '任务', '任務', 'المهمة', 'Задача'],
  project: ['Project', 'プロジェクト', '项目', '專案', 'المشروع', 'Проект'],
  from: ['From', '送信者', '发送方', '寄件者', 'من', 'От'],
  to: ['To', '受信者', '接收方', '收件者', 'إلى', 'Кому'],
  messageId: ['Message ID', 'メッセージ ID', '消息 ID', '訊息 ID', 'معرّف الرسالة', 'ID сообщения'],
  sent: ['Sent', '送信日時', '发送时间', '傳送時間', 'أُرسلت', 'Отправлено'],
  replyTo: ['Reply to message', '返信先メッセージ', '回复消息', '回覆訊息', 'رد على الرسالة', 'Ответ на сообщение'],
  read: [
    'Read by recipient runtime',
    '受信側ランタイムが読み取り済み',
    '接收方运行时已读取',
    '收件者執行環境已讀取',
    'قرأتها بيئة تشغيل المستلم',
    'Прочитано средой выполнения получателя'
  ],
  unread: [
    'Not yet read by recipient runtime',
    '受信側ランタイムは未読',
    '接收方运行时尚未读取',
    '收件者執行環境尚未讀取',
    'لم تقرأها بيئة تشغيل المستلم بعد',
    'Ещё не прочитано средой выполнения получателя'
  ],
  messages: ['Messages', 'メッセージ', '消息', '訊息', 'الرسائل', 'Сообщения'],
  unknownTeam: [
    'Team not recorded',
    'チーム未記録',
    '未记录团队',
    '未記錄團隊',
    'الفريق غير مسجّل',
    'Команда не указана'
  ]
} as const

export type OrganizationConversationsCopy = { [K in keyof typeof copy]: string }

const forLocale = (index: number): OrganizationConversationsCopy =>
  Object.fromEntries(Object.entries(copy).map(([key, values]) => [key, values[index]])) as OrganizationConversationsCopy

export const organizationConversationsEn = forLocale(0)
export const organizationConversationsJa = forLocale(1)
export const organizationConversationsZh = forLocale(2)
export const organizationConversationsZhHant = forLocale(3)
export const organizationConversationsAr = forLocale(4)
export const organizationConversationsRu = forLocale(5)
