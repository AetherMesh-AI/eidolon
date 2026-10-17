import type { Locale } from './types'

interface OrganizationSelectionCopy {
  title: string
  description: string
  reasons: Record<'pending' | 'unavailable' | 'storage' | 'invalid', string>
  profileReference: string
  retry: string
  retryHint: string
  retryUnavailable: string
  openStartup: string
  startupHint: string
  actionFailed: string
}

const copy: Record<Locale, OrganizationSelectionCopy> = {
  en: {
    title: 'Organization selection needs your attention',
    description: 'The saved selection needs confirmation before it can be resumed. Review the recovery action below.',
    reasons: {
      pending: 'The previous organization selection did not finish. It will not continue automatically.',
      unavailable: 'The saved organization could not be restored.',
      storage: 'The app could not access the saved organization selection.',
      invalid: 'The saved organization selection is not valid and cannot be retried.'
    },
    profileReference: 'Saved profile reference:',
    retry: 'Retry',
    retryHint: 'Choose Retry to verify and continue the saved organization selection.',
    retryUnavailable: 'This saved selection cannot be retried. You can explicitly open the startup workspace.',
    openStartup: 'Open startup workspace',
    startupHint: 'Opening the startup workspace clears the saved organization selection. This happens only if you choose it.',
    actionFailed: 'That action could not be completed. This recovery stays open. Try again when available, or choose the startup workspace.'
  },
  zh: {
    title: '组织选择需要你处理',
    description: '已保存的选择需要确认后才能恢复。请查看下方的恢复操作。',
    reasons: {
      pending: '上一次组织选择尚未完成。它不会自动继续。',
      unavailable: '无法恢复已保存的组织。',
      storage: '应用无法访问已保存的组织选择。',
      invalid: '已保存的组织选择无效，无法重试。'
    },
    profileReference: '已保存的配置档案引用：',
    retry: '重试',
    retryHint: '请选择“重试”以验证并继续已保存的组织选择。',
    retryUnavailable: '无法重试此已保存的选择。你可以明确选择打开启动工作区。',
    openStartup: '打开启动工作区',
    startupHint: '打开启动工作区会清除已保存的组织选择。只有在你明确选择后才会执行。',
    actionFailed: '无法完成该操作。此恢复界面将保持打开。请在可用时重试，或选择启动工作区。'
  },
  'zh-hant': {
    title: '組織選擇需要你處理',
    description: '已儲存的選擇需要確認後才能恢復。請查看下方的復原操作。',
    reasons: {
      pending: '上一次組織選擇尚未完成。它不會自動繼續。',
      unavailable: '無法還原已儲存的組織。',
      storage: '應用程式無法存取已儲存的組織選擇。',
      invalid: '已儲存的組織選擇無效，無法重試。'
    },
    profileReference: '已儲存的設定檔參照：',
    retry: '重試',
    retryHint: '請選擇「重試」以驗證並繼續已儲存的組織選擇。',
    retryUnavailable: '無法重試此已儲存的選擇。你可以明確選擇開啟啟動工作區。',
    openStartup: '開啟啟動工作區',
    startupHint: '開啟啟動工作區會清除已儲存的組織選擇。只有在你明確選擇後才會執行。',
    actionFailed: '無法完成該操作。此復原介面將保持開啟。請在可用時重試，或選擇啟動工作區。'
  },
  ja: {
    title: '組織の選択を確認してください',
    description: '保存した選択を再開するには確認が必要です。以下の復旧操作を確認してください。',
    reasons: {
      pending: '前回の組織選択が完了していません。自動的には続行しません。',
      unavailable: '保存された組織を復元できませんでした。',
      storage: '保存された組織選択にアクセスできませんでした。',
      invalid: '保存された組織選択が無効なため、再試行できません。'
    },
    profileReference: '保存されたプロファイルの参照：',
    retry: '再試行',
    retryHint: '「再試行」を選ぶと、保存された組織選択を検証して続行します。',
    retryUnavailable: 'この保存された選択は再試行できません。起動時のワークスペースを明示的に選んで開くことができます。',
    openStartup: '起動時のワークスペースを開く',
    startupHint: '起動時のワークスペースを開くと、保存された組織選択が消去されます。明示的に選んだ場合にのみ実行されます。',
    actionFailed: '操作を完了できませんでした。この復旧画面は開いたままです。利用可能になったら再試行するか、起動時のワークスペースを選んでください。'
  },
  ar: {
    title: 'اختيار المؤسسة يحتاج إلى انتباهك',
    description: 'يحتاج الاختيار المحفوظ إلى تأكيد قبل استئنافه. راجع إجراء الاستعادة أدناه.',
    reasons: {
      pending: 'لم يكتمل اختيار المؤسسة السابق. لن تتم متابعته تلقائيًا.',
      unavailable: 'تعذرت استعادة المؤسسة المحفوظة.',
      storage: 'تعذر على التطبيق الوصول إلى اختيار المؤسسة المحفوظ.',
      invalid: 'اختيار المؤسسة المحفوظ غير صالح ولا يمكن إعادة المحاولة.'
    },
    profileReference: 'مرجع ملف التعريف المحفوظ:',
    retry: 'إعادة المحاولة',
    retryHint: 'اختر «إعادة المحاولة» للتحقق من اختيار المؤسسة المحفوظ ومتابعته.',
    retryUnavailable: 'لا يمكن إعادة محاولة هذا الاختيار المحفوظ. يمكنك اختيار فتح مساحة عمل بدء التشغيل صراحةً.',
    openStartup: 'فتح مساحة عمل بدء التشغيل',
    startupHint: 'يؤدي فتح مساحة عمل بدء التشغيل إلى مسح اختيار المؤسسة المحفوظ. لن يحدث ذلك إلا إذا اخترته صراحةً.',
    actionFailed: 'تعذر إكمال هذا الإجراء. ستظل شاشة الاستعادة مفتوحة. أعد المحاولة عندما تصبح متاحة، أو اختر مساحة عمل بدء التشغيل.'
  },
  ru: {
    title: 'Выбор организации требует вашего внимания',
    description: 'Для возобновления сохранённого выбора требуется подтверждение. Проверьте действие восстановления ниже.',
    reasons: {
      pending: 'Предыдущий выбор организации не был завершён. Он не будет продолжен автоматически.',
      unavailable: 'Не удалось восстановить сохранённую организацию.',
      storage: 'Приложению не удалось получить доступ к сохранённому выбору организации.',
      invalid: 'Сохранённый выбор организации недействителен. Повторная попытка невозможна.'
    },
    profileReference: 'Сохранённая ссылка на профиль:',
    retry: 'Повторить',
    retryHint: 'Нажмите «Повторить», чтобы проверить и продолжить сохранённый выбор организации.',
    retryUnavailable: 'Этот сохранённый выбор нельзя повторить. Вы можете явно выбрать открытие стартовой рабочей области.',
    openStartup: 'Открыть стартовую рабочую область',
    startupHint: 'Открытие стартовой рабочей области удалит сохранённый выбор организации. Это произойдёт только по вашему явному выбору.',
    actionFailed: 'Не удалось завершить действие. Этот экран восстановления останется открытым. Повторите попытку, когда она станет доступна, или выберите стартовую рабочую область.'
  }
}

export function getOrganizationSelectionCopy(locale: Locale): OrganizationSelectionCopy {
  return copy[locale]
}
