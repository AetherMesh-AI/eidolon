import type { OrganizationHomeCopy } from './organization-home'

type MemberMessages = OrganizationHomeCopy['memberMessages']

export const organizationMemberMessagesJa = {
  members: 'メンバー', search: 'メンバーを検索', noMatches: '検索条件に一致するメンバーはいません。',
  directoryNote: 'このプロファイルの永続メンバーです。選択しても会話は開かず、利用枠も追加されません。',
  conversation: 'メンバーとの会話',
  selectNote: 'メンバーを選び、「チャット」を選択すると、保存された会話の閲覧やメッセージの送信ができます。',
  memberContext: 'メンバーの背景情報', responsibilities: '担当業務', linkedWork: '関連する目標',
  linkedWorkNote: 'この表示に記録された担当者、および現在のタスクまたはマネージャーへの割り当て。',
  noLinkedWork: 'この表示には関連する目標が記録されていません。', retainedContext: '識別情報、メモリ、履歴'
} satisfies MemberMessages

export const organizationMemberMessagesZh = {
  members: '成员', search: '搜索成员', noMatches: '没有符合搜索条件的成员。',
  directoryNote: '此配置文件中的持久成员。选择成员不会打开对话或补充其额度。',
  conversation: '成员对话', selectNote: '选择成员，然后选择“聊天”，即可查看保留的对话或发送消息。',
  memberContext: '成员背景', responsibilities: '职责', linkedWork: '关联目标',
  linkedWorkNote: '此视图中记录的负责人，以及当前的任务或经理工作分配。',
  noLinkedWork: '此视图中没有记录关联目标。', retainedContext: '身份、记忆与历史'
} satisfies MemberMessages

export const organizationMemberMessagesZhHant = {
  members: '成員', search: '搜尋成員', noMatches: '沒有符合搜尋條件的成員。',
  directoryNote: '此設定檔中的持久成員。選取成員不會開啟對話或補充其額度。',
  conversation: '成員對話', selectNote: '選取成員，再選擇「聊天」，即可檢視保留的對話或傳送訊息。',
  memberContext: '成員背景', responsibilities: '職責', linkedWork: '關聯目標',
  linkedWorkNote: '此檢視中記錄的負責人，以及目前的任務或經理工作指派。',
  noLinkedWork: '此檢視中沒有記錄關聯目標。', retainedContext: '身分、記憶與歷史'
} satisfies MemberMessages

export const organizationMemberMessagesAr = {
  members: 'الأعضاء', search: 'البحث عن أعضاء', noMatches: 'لا يوجد أعضاء يطابقون هذا البحث.',
  directoryNote: 'أعضاء دائمون في هذا الملف الشخصي. لا يؤدي اختيار عضو إلى فتح محادثته أو تجديد حصتها.',
  conversation: 'محادثة العضو', selectNote: 'اختر عضواً، ثم اختر «دردشة» لقراءة المحادثة المحفوظة أو إرسال رسالة.',
  memberContext: 'سياق العضو', responsibilities: 'المسؤوليات', linkedWork: 'الأهداف المرتبطة',
  linkedWorkNote: 'الملكية المسجلة وتكليفات المهام أو المديرين الحالية في هذا العرض.',
  noLinkedWork: 'لا توجد أهداف مرتبطة مسجلة في هذا العرض.', retainedContext: 'الهوية والذاكرة والسجل'
} satisfies MemberMessages

export const organizationMemberMessagesRu = {
  members: 'Участники', search: 'Поиск участников', noMatches: 'Участники по этому запросу не найдены.',
  directoryNote: 'Постоянные участники этого профиля. Выбор участника не открывает диалог и не пополняет его лимит.',
  conversation: 'Диалог с участником', selectNote: 'Выберите участника, затем «Чат», чтобы прочитать сохранённый диалог или отправить сообщение.',
  memberContext: 'Контекст участника', responsibilities: 'Обязанности', linkedWork: 'Связанные цели',
  linkedWorkNote: 'Указанные владельцы и текущие назначения задач или работ менеджерам в этом представлении.',
  noLinkedWork: 'В этом представлении не зафиксировано связанных целей.', retainedContext: 'Идентичность, память и история'
} satisfies MemberMessages
