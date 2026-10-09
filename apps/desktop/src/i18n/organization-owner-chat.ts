/** Owner discussion remains separate from work requests and permission decisions. */
const copy = {
  profileRoute: ['Configured profile defaults', '設定済みプロファイルの既定値', '已配置的配置文件默认值', '已設定的設定檔預設值', 'الإعدادات الافتراضية للملف المهيأ', 'Настройки выбранного профиля'],
  providerUsage: ['Replies use the configured provider and may incur usage charges.', '返信は設定済みプロバイダーを使用し、利用料金が発生する場合があります。', '回复使用已配置的提供商，可能产生使用费用。', '回覆使用已設定的供應商，可能產生使用費用。', 'تستخدم الردود المزوّد المهيأ وقد تترتب عليها رسوم استخدام.', 'Ответы используют настроенного провайдера и могут повлечь плату за использование.'],
  heading: [
    'Owner conversation',
    'オーナーとの会話',
    '所有者对话',
    '擁有者對話',
    'محادثة المالك',
    'Диалог с владельцем'
  ],
  chat: ['Chat', 'チャット', '聊天', '聊天', 'دردشة', 'Чат'],
  call: ['Call', '通話', '通话', '通話', 'اتصال', 'Звонок'],
  callUnavailable: [
    'Voice calls are not available yet.',
    '音声通話はまだ利用できません。',
    '语音通话尚不可用。',
    '語音通話尚未開放。',
    'المكالمات الصوتية غير متاحة بعد.',
    'Голосовые звонки пока недоступны.'
  ],
  hide: ['Hide conversation', '会話を閉じる', '收起对话', '收起對話', 'إخفاء المحادثة', 'Скрыть диалог'],
  note: [
    'Discussion only. Replies use the member’s public identity and this conversation, with no tools or private work history. Chat does not create work, answer formal questions, or grant permissions. Use the explicit actions below for those decisions.',
    '相談専用です。メンバーの公開情報とこの会話を使い、ツールや非公開の作業履歴は使いません。チャットでは作業の作成、正式な質問への回答、権限の付与は行いません。決定には下記の操作を使ってください。',
    '仅供讨论。回复使用成员的公开身份和此对话，不使用工具或私人工作历史。聊天不会创建工作、回答正式问题或授予权限。请使用下方的明确操作作出这些决定。',
    '僅供討論。回覆使用成員的公開身分和此對話，不使用工具或私人工作紀錄。聊天不會建立工作、回答正式問題或授予權限。請使用下方的明確操作作出這些決定。',
    'للنقاش فقط. تستخدم الردود هوية العضو العامة وهذه المحادثة دون أدوات أو سجل عمل خاص. لا تنشئ الدردشة عملاً ولا تجيب عن أسئلة رسمية ولا تمنح أذونات. استخدم الإجراءات الصريحة أدناه لهذه القرارات.',
    'Только обсуждение. Ответы используют публичную личность участника и этот диалог, без инструментов и закрытой истории работы. Чат не создаёт задачи, не отвечает на формальные вопросы и не выдаёт разрешения. Для этого используйте явные действия ниже.'
  ],
  unavailable: [
    'This runtime does not support member chat. Update the runtime to use it.',
    'このランタイムはメンバーチャットに未対応です。更新してください。',
    '此运行时不支持成员聊天，请更新运行时。',
    '此執行環境不支援成員聊天，請更新執行環境。',
    'بيئة التشغيل لا تدعم دردشة الأعضاء. حدّثها لاستخدامها.',
    'Среда выполнения не поддерживает чат участников. Обновите её.'
  ],
  offline: [
    'Reconnect to check this conversation before sending.',
    '送信前に再接続して会話を確認してください。',
    '请重新连接并检查对话后再发送。',
    '請重新連線並檢查對話後再傳送。',
    'أعد الاتصال للتحقق من المحادثة قبل الإرسال.',
    'Переподключитесь и проверьте диалог перед отправкой.'
  ],
  readOnly: [
    'This member is inactive or retired. Conversation history is read-only.',
    'このメンバーは無効または引退済みです。履歴は閲覧のみ可能です。',
    '此成员已停用或退役，对话历史为只读。',
    '此成員已停用或退役，對話紀錄為唯讀。',
    'هذا العضو غير نشط أو متقاعد. سجل المحادثة للقراءة فقط.',
    'Участник неактивен или выведен из состава. История доступна только для чтения.'
  ],
  empty: [
    'Start a conversation with this member',
    'このメンバーと会話を始める',
    '与此成员开始对话',
    '與此成員開始對話',
    'ابدأ محادثة مع هذا العضو',
    'Начните диалог с участником'
  ],
  loading: [
    'Retrieving conversation',
    '会話を取得中',
    '正在获取对话',
    '正在取得對話',
    'جارٍ استرجاع المحادثة',
    'Получение диалога'
  ],
  error: [
    'Could not verify the conversation',
    '会話を確認できませんでした',
    '无法验证对话',
    '無法驗證對話',
    'تعذر التحقق من المحادثة',
    'Не удалось проверить диалог'
  ],
  invalid: [
    'The runtime returned an invalid or mismatched owner conversation.',
    'ランタイムが無効または異なる会話を返しました。',
    '运行时返回了无效或不匹配的所有者对话。',
    '執行環境傳回無效或不符的擁有者對話。',
    'أعادت بيئة التشغيل محادثة غير صالحة أو غير مطابقة.',
    'Среда выполнения вернула некорректный или чужой диалог.'
  ],
  changed: [
    'The conversation changed. Check its latest state before sending.',
    '会話が変更されました。最新状態を確認してください。',
    '对话已更改，请检查最新状态后再发送。',
    '對話已變更，請先檢查最新狀態。',
    'تغيرت المحادثة. تحقق من حالتها قبل الإرسال.',
    'Диалог изменился. Проверьте его состояние перед отправкой.'
  ],
  refresh: ['Check conversation', '会話を確認', '检查对话', '檢查對話', 'تحقق من المحادثة', 'Проверить диалог'],
  message: ['Message', 'メッセージ', '消息', '訊息', 'رسالة', 'Сообщение'],
  placeholder: [
    'Discuss an idea or share context…',
    'アイデアや背景を共有…',
    '讨论想法或分享背景…',
    '討論想法或分享背景…',
    'ناقش فكرة أو شارك السياق…',
    'Обсудите идею или поделитесь контекстом…'
  ],
  send: ['Send message', 'メッセージを送信', '发送消息', '傳送訊息', 'إرسال الرسالة', 'Отправить сообщение'],
  sending: [
    'Sending message',
    'メッセージを送信中',
    '正在发送消息',
    '正在傳送訊息',
    'جارٍ إرسال الرسالة',
    'Отправка сообщения'
  ],
  cancel: ['Cancel reply', '返信をキャンセル', '取消回复', '取消回覆', 'إلغاء الرد', 'Отменить ответ'],
  cancelling: [
    'Cancelling reply',
    '返信をキャンセル中',
    '正在取消回复',
    '正在取消回覆',
    'جارٍ إلغاء الرد',
    'Отмена ответа'
  ],
  cancelUncertain: [
    'Cancellation is not confirmed. Check the conversation; a late reply may still arrive.',
    'キャンセルは未確認です。会話を確認してください。返信が届く場合があります。',
    '取消尚未确认，请检查对话；回复仍可能稍后到达。',
    '取消尚未確認，請檢查對話；回覆仍可能稍後到達。',
    'لم يتأكد الإلغاء. تحقق من المحادثة؛ قد يصل رد متأخر.',
    'Отмена не подтверждена. Проверьте диалог: ответ ещё может прийти.'
  ],
  uncertain: [
    'Delivery is not confirmed. Check the conversation or retry this same message safely. A retry will not create a second reply.',
    '送信は未確認です。会話を確認するか同じメッセージを再試行してください。返信は重複しません。',
    '发送尚未确认。请检查对话或安全重试同一条消息，重试不会产生重复回复。',
    '傳送尚未確認。請檢查對話或安全重試同一則訊息，重試不會產生重複回覆。',
    'لم يتأكد التسليم. تحقق من المحادثة أو أعد إرسال الرسالة نفسها بأمان؛ لن ينشئ ذلك رداً ثانياً.',
    'Доставка не подтверждена. Проверьте диалог или повторите отправку того же сообщения. Повтор не создаст второй ответ.'
  ],
  retry: [
    'Retry same message',
    '同じメッセージを再試行',
    '重试同一条消息',
    '重試同一則訊息',
    'إعادة إرسال الرسالة نفسها',
    'Повторить то же сообщение'
  ],
  pending: ['Waiting for reply', '返信待ち', '等待回复', '等待回覆', 'بانتظار الرد', 'Ожидание ответа'],
  running: ['Writing a reply', '返信を作成中', '正在撰写回复', '正在撰寫回覆', 'جارٍ كتابة الرد', 'Подготовка ответа'],
  completed: ['Replied', '返信済み', '已回复', '已回覆', 'تم الرد', 'Ответ получен'],
  cancelled: ['Reply cancelled', '返信をキャンセルしました', '回复已取消', '回覆已取消', 'أُلغي الرد', 'Ответ отменён'],
  timed_out: [
    'Reply timed out',
    '返信がタイムアウトしました',
    '回复超时',
    '回覆逾時',
    'انتهت مهلة الرد',
    'Время ожидания истекло'
  ],
  turnUncertain: [
    'Reply interrupted; completion is unknown',
    '返信が中断され、完了は不明です',
    '回复中断，完成状态未知',
    '回覆中斷，完成狀態未知',
    'انقطع الرد؛ حالة الاكتمال غير معروفة',
    'Ответ прерван; завершение неизвестно'
  ],
  blocked: ['Reply blocked', '返信を生成できません', '回复受阻', '回覆受阻', 'الرد محظور', 'Ответ заблокирован'],
  you: ['You', 'あなた', '你', '你', 'أنت', 'Вы'],
  messages: [
    'Conversation messages',
    '会話のメッセージ',
    '对话消息',
    '對話訊息',
    'رسائل المحادثة',
    'Сообщения диалога'
  ],
  replyTo: ['Reply to message', '返信先メッセージ', '回复消息', '回覆訊息', 'رد على الرسالة', 'Ответ на сообщение'],
  replyingTo: ['Continuing after', '続きの対象', '接续消息', '接續訊息', 'متابعة بعد', 'Продолжение после'],
  calls: ['Replies remaining', '残りの返信回数', '剩余回复次数', '剩餘回覆次數', 'الردود المتبقية', 'Осталось ответов'],
  tokens: [
    'Reserved tokens',
    '予約済みトークン',
    '已预留令牌',
    '已預留權杖',
    'الرموز المحجوزة',
    'Зарезервировано токенов'
  ],
  limits: [
    'Characters per message',
    'メッセージの文字数上限',
    '每条消息字数上限',
    '每則訊息字數上限',
    'الأحرف لكل رسالة',
    'Символов в сообщении'
  ],
  budgetNote: [
    'Limits apply for the lifetime of this conversation. Cancelled and interrupted replies can still use budget.',
    '上限はこの会話全体に適用されます。中断やキャンセルでも予算を消費する場合があります。',
    '限制适用于此对话的整个生命周期。取消或中断的回复仍可能消耗额度。',
    '限制適用於此對話的整個生命週期。取消或中斷的回覆仍可能消耗額度。',
    'تسري الحدود طوال عمر المحادثة. قد تستهلك الردود الملغاة أو المنقطعة من الميزانية.',
    'Лимиты действуют на весь срок диалога. Отменённые и прерванные ответы тоже могут расходовать бюджет.'
  ],
  exhausted: [
    'This conversation has reached its usage limit. History is still available. Continuing beyond this limit is not available in this version.',
    'この会話の利用上限に達しました。履歴は閲覧できます。このバージョンでは上限を超えて続けられません。',
    '此对话已达到使用上限，仍可查看历史。此版本不支持超出上限后继续。',
    '此對話已達使用上限，仍可檢視紀錄。此版本不支援超出上限後繼續。',
    'بلغت المحادثة حد الاستخدام. السجل ما زال متاحاً. لا يدعم هذا الإصدار المتابعة بعد هذا الحد.',
    'Диалог достиг лимита использования. История остаётся доступной. Эта версия не поддерживает продолжение сверх лимита.'
  ],
  createObjective: ['Create objective', '目標を作成', '创建目标', '建立目標', 'إنشاء هدف', 'Создать цель'],
  needsYou: ['Needs you', 'あなたの対応待ち', '需要你处理', '需要你處理', 'بحاجة إليك', 'Требуется ваше решение'],
  pendingNote: [
    'Unresolved requests remain open. Discussing them here does not answer or dismiss them.',
    '未解決のリクエストは開いたままです。ここでの相談は回答や終了にはなりません。',
    '未解决的请求仍然保持开放。在此讨论不会回答或关闭请求。',
    '未解決的請求仍然保持開啟。在此討論不會回答或關閉請求。',
    'الطلبات غير المحلولة تبقى مفتوحة. مناقشتها هنا لا تجيب عنها ولا تغلقها.',
    'Нерешённые запросы остаются открытыми. Обсуждение здесь не отвечает на них и не закрывает их.'
  ]
} as const

export type OrganizationOwnerChatCopy = { [K in keyof typeof copy]: string }

const forLocale = (index: number): OrganizationOwnerChatCopy =>
  Object.fromEntries(Object.entries(copy).map(([key, values]) => [key, values[index]])) as OrganizationOwnerChatCopy

export const organizationOwnerChatEn = forLocale(0)
export const organizationOwnerChatJa = forLocale(1)
export const organizationOwnerChatZh = forLocale(2)
export const organizationOwnerChatZhHant = forLocale(3)
export const organizationOwnerChatAr = forLocale(4)
export const organizationOwnerChatRu = forLocale(5)
