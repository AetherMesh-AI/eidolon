import type { Locale } from './types'

const messages = {
  title: ['ChatGPT setup connection', 'ChatGPTセットアップ接続', 'ChatGPT 设置连接', 'ChatGPT 設定連線', 'اتصال ChatGPT للإعداد', 'Подключение ChatGPT для настройки'],
  purpose: [
    'This organization’s dedicated ChatGPT connection is only for starter-team Generate. It does not power ongoing organization work. Currently available only to a verified local owner on macOS.',
    'この組織専用のChatGPT接続は、初期チームの生成にのみ使用します。組織の継続的な業務には使用しません。現在はmacOSの確認済みローカル所有者のみ利用できます。',
    '此组织的专用 ChatGPT 连接仅用于初始团队的生成，不用于组织的持续工作。目前仅适用于 macOS 上经过验证的本地所有者。',
    '此組織的專用 ChatGPT 連線僅用於初始團隊的產生，不用於組織的持續工作。目前僅適用於 macOS 上經過驗證的本機擁有者。',
    'اتصال ChatGPT المخصص لهذه المؤسسة مخصص فقط لتوليد الفريق الأولي. ولا يشغّل أعمال المؤسسة المستمرة. متاح حالياً فقط للمالك المحلي المُتحقق منه على macOS.',
    'Отдельное подключение ChatGPT этой организации используется только для генерации начальной команды. Оно не обеспечивает текущую работу организации. Сейчас доступно только подтверждённому локальному владельцу на macOS.'
  ],
  loading: ['Checking setup connection…', 'セットアップ接続を確認中…', '正在检查设置连接…', '正在檢查設定連線…', 'جارٍ فحص اتصال الإعداد…', 'Проверка подключения для настройки…'],
  unknown: ['Connection status is unknown. Check again or explicitly recover retained state.', '接続状態は不明です。再確認するか、保持された状態を明示的に復旧してください。', '连接状态未知。请重新检查或明确恢复保留的状态。', '連線狀態未知。請重新檢查或明確復原保留的狀態。', 'حالة الاتصال غير معروفة. افحصها مجدداً أو استعد الحالة المحفوظة صراحةً.', 'Состояние подключения неизвестно. Проверьте снова или явно восстановите сохранённое состояние.'],
  check: ['Check setup connection', 'セットアップ接続を確認', '检查设置连接', '檢查設定連線', 'فحص اتصال الإعداد', 'Проверить подключение для настройки'],
  recover: ['Recover connection state', '接続状態を復旧', '恢复连接状态', '復原連線狀態', 'استعادة حالة الاتصال', 'Восстановить состояние подключения'],
  disconnect: ['Disconnect setup connection', 'セットアップ接続を切断', '断开设置连接', '中斷設定連線', 'فصل اتصال الإعداد', 'Отключить подключение для настройки'],
  active: ['Setup connection saved', 'セットアップ接続を保存済み', '设置连接已保存', '設定連線已儲存', 'اتصال الإعداد محفوظ', 'Подключение для настройки сохранено'],
  refreshing: ['Connection refresh is unresolved. You can recover its state or disconnect locally.', '接続の更新は未解決です。状態の復旧またはローカルでの切断ができます。', '连接刷新尚未解决。可以恢复状态或在本地断开连接。', '連線更新尚未解決。可復原狀態或在本機中斷連線。', 'تحديث الاتصال غير محسوم. يمكنك استعادة حالته أو فصله محلياً.', 'Обновление подключения не завершено. Можно восстановить состояние или отключить его локально.'],
  absent: ['No setup connection saved', '保存されたセットアップ接続はありません', '未保存设置连接', '未儲存設定連線', 'لا يوجد اتصال إعداد محفوظ', 'Подключение для настройки не сохранено'],
  disconnected: ['Setup connection disconnected locally', 'セットアップ接続をローカルで切断しました', '设置连接已在本地断开', '設定連線已在本機中斷', 'تم فصل اتصال الإعداد محلياً', 'Подключение для настройки отключено локально'],
  remoteUnknown: [
    'Local access is retired. Remote revocation is not confirmed; usage already in progress may continue.',
    'ローカルのアクセスは無効化されました。提供元での失効は未確認で、開始済みの利用が続く場合があります。',
    '本地访问已停用。尚未确认远程撤销；已经开始的使用可能继续。',
    '本機存取已停用。尚未確認遠端撤銷；已開始的使用可能繼續。',
    'تم إيقاف الوصول المحلي. لم يُؤكد الإلغاء لدى المزوّد؛ وقد يستمر الاستخدام الذي بدأ بالفعل.',
    'Локальный доступ прекращён. Отзыв у провайдера не подтверждён; уже начатое использование может продолжиться.'
  ],
  cleanup: ['Local cleanup is pending. Recover the connection state to check it.', 'ローカルの後処理が保留中です。接続状態を復旧して確認してください。', '本地清理尚未完成。请恢复连接状态进行检查。', '本機清理尚未完成。請復原連線狀態以檢查。', 'التنظيف المحلي معلّق. استعد حالة الاتصال للتحقق.', 'Локальная очистка ожидает завершения. Восстановите состояние подключения для проверки.'],
  setupFinished: [
    'Reconnect is unavailable after the team is created or organization work has begun. You can still check, recover or disconnect this setup-only connection here.',
    'チーム作成後または組織の業務開始後は再接続できません。ここでセットアップ専用接続の確認、復旧、切断は引き続き行えます。',
    '创建团队或开始组织工作后，无法重新连接。仍可在此检查、恢复或断开此仅用于设置的连接。',
    '建立團隊或開始組織工作後，無法重新連線。仍可在此檢查、復原或中斷此僅用於設定的連線。',
    'لا تتاح إعادة الاتصال بعد إنشاء الفريق أو بدء أعمال المؤسسة. لا يزال بإمكانك فحص اتصال الإعداد هذا أو استعادة حالته أو فصله هنا.',
    'Повторное подключение недоступно после создания команды или начала работы организации. Здесь по-прежнему можно проверить, восстановить состояние или отключить это подключение для настройки.'
  ],
  freshOnly: ['Connecting requires an eligible fresh organization and explicit consent in team setup.', '接続には未使用の適格な組織とチーム設定での明示的な同意が必要です。', '连接需要符合条件的新组织，并在团队设置中明确授权。', '連線需要符合條件的新組織，並在團隊設定中明確授權。', 'يتطلب الاتصال مؤسسة جديدة مؤهلة وموافقة صريحة ضمن إعداد الفريق.', 'Для подключения нужна новая подходящая организация и явное согласие при настройке команды.'],
  openSetup: ['Open team setup on Home', 'ホームでチーム設定を開く', '在主页打开团队设置', '在首頁開啟團隊設定', 'فتح إعداد الفريق في الصفحة الرئيسية', 'Открыть настройку команды на главной'],
  unavailable: ['Connect as this organization’s verified local owner to manage its setup connection.', 'この組織の確認済みローカル所有者として接続すると、セットアップ接続を管理できます。', '请以此组织经过验证的本地所有者身份连接，以管理设置连接。', '請以此組織經過驗證的本機擁有者身分連線，以管理設定連線。', 'اتصل بصفتك المالك المحلي المُتحقق منه لهذه المؤسسة لإدارة اتصال الإعداد.', 'Для управления подключением войдите как подтверждённый локальный владелец этой организации.'],
  error: ['Connection state could not be verified. Check again before taking another action. This currently requires a verified local owner on macOS; Linux, Windows and remote connections are unsupported.', '接続状態を確認できませんでした。次の操作前に再確認してください。現在はmacOSの確認済みローカル所有者が必要です。Linux、Windows、リモート接続は未対応です。', '无法验证连接状态。请在执行其他操作前重新检查。目前需要 macOS 上经过验证的本地所有者；不支持 Linux、Windows 和远程连接。', '無法驗證連線狀態。請在執行其他操作前重新檢查。目前需要 macOS 上經過驗證的本機擁有者；不支援 Linux、Windows 及遠端連線。', 'تعذر التحقق من حالة الاتصال. افحصها مجدداً قبل إجراء آخر. يلزم حالياً مالك محلي مُتحقق منه على macOS؛ لا يُدعم Linux أو Windows أو الاتصال البعيد.', 'Не удалось проверить состояние подключения. Проверьте его снова перед следующим действием. Сейчас требуется подтверждённый локальный владелец на macOS; Linux, Windows и удалённые подключения не поддерживаются.'],
  disconnectNote: [
    'Disconnect retires local access without deleting your team, setup history or prior charges. Remote revocation may be unavailable. Recovery checks retained state and does not retry model work.',
    '切断すると、チーム、設定履歴、過去の料金を削除せずにローカルのアクセスを無効化します。提供元で失効できない場合があります。復旧は保持された状態を確認し、モデル処理を再試行しません。',
    '断开连接会停用本地访问，不会删除团队、设置历史或既有费用。远程撤销可能不可用。恢复操作检查保留的状态，不会重试模型工作。',
    '中斷連線會停用本機存取，不會刪除團隊、設定歷史或既有費用。遠端撤銷可能無法使用。復原操作檢查保留的狀態，不會重試模型工作。',
    'يوقف الفصل الوصول المحلي دون حذف الفريق أو سجل الإعداد أو الرسوم السابقة. قد لا يتاح الإلغاء لدى المزوّد. تفحص الاستعادة الحالة المحفوظة ولا تعيد محاولة عمل النموذج.',
    'Отключение прекращает локальный доступ, сохраняя команду, историю настройки и прошлые списания. Отзыв у провайдера может быть недоступен. Восстановление проверяет сохранённое состояние и не повторяет работу модели.'
  ]
} as const

export type SetupConnectionCopy = { [K in keyof typeof messages]: string }

const forLocale = (index: number): SetupConnectionCopy =>
  Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as SetupConnectionCopy

export const setupConnectionCopy: Record<Locale, SetupConnectionCopy> = {
  en: forLocale(0), ja: forLocale(1), zh: forLocale(2), 'zh-hant': forLocale(3), ar: forLocale(4), ru: forLocale(5)
}
