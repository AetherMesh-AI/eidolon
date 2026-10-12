import type { ActivationResult } from '@/app/eidolon/organization-directory-activation'

import type { Locale } from './types'

export type DirectoryActivationCopy = Record<
  ActivationResult['status'] | 'confirm' | 'cancel' | 'close' | 'pending' | 'explanation',
  string
>

export const directoryActivationCopy: Record<Locale, DirectoryActivationCopy> = {
  en: {
    confirm: 'Confirm organization selection',
    cancel: 'Cancel selection',
    close: 'Close',
    explanation:
      'Switch to this saved connection and profile, then verify the organization. This does not start or resume work.',
    pending: 'Selecting and verifying the organization. Selection has started and cannot be cancelled here.',
    verified: 'Selected organization verified.',
    'committed-unverified':
      'The selected organization could not be verified. The connection may have changed. Check the current organization before continuing.',
    unchanged: 'The connection did not change. Selection was not completed.',
    superseded: 'Another selection took precedence. Check the current organization.',
    unavailable: 'This saved organization is no longer available for selection.',
    disabled: 'Organization selection is not enabled.',
    cancelled: 'Selection cancelled.'
  },
  ja: {
    confirm: '組織の選択を確定',
    cancel: '選択をキャンセル',
    close: '閉じる',
    explanation: '保存済みの接続とプロファイルに切り替え、組織を確認します。作業は開始・再開しません。',
    pending: '組織を選択して確認しています。選択は開始済みのため、ここではキャンセルできません。',
    verified: '選択した組織を確認しました。',
    'committed-unverified':
      '選択した組織を確認できませんでした。接続が変わった可能性があります。続行する前に現在の組織を確認してください。',
    unchanged: '接続は変更されませんでした。選択は完了していません。',
    superseded: '別の選択が優先されました。現在の組織を確認してください。',
    unavailable: 'この保存済みの組織は選択できなくなりました。',
    disabled: '組織の選択は有効になっていません。',
    cancelled: '選択をキャンセルしました。'
  },
  zh: {
    confirm: '确认选择组织',
    cancel: '取消选择',
    close: '关闭',
    explanation: '切换到此已保存的连接和配置，再验证组织。这不会开始或恢复工作。',
    pending: '正在选择并验证组织。选择已开始，无法在此取消。',
    verified: '已验证所选组织。',
    'committed-unverified': '无法验证所选组织。连接可能已更改。继续前请检查当前组织。',
    unchanged: '连接未更改。选择未完成。',
    superseded: '另一项选择已优先处理。请检查当前组织。',
    unavailable: '此已保存的组织已无法选择。',
    disabled: '组织选择尚未启用。',
    cancelled: '已取消选择。'
  },
  'zh-hant': {
    confirm: '確認選取組織',
    cancel: '取消選取',
    close: '關閉',
    explanation: '切換至此已儲存的連線和設定檔，再驗證組織。這不會開始或繼續工作。',
    pending: '正在選取並驗證組織。選取已開始，無法在此取消。',
    verified: '已驗證所選組織。',
    'committed-unverified': '無法驗證所選組織。連線可能已變更。繼續前請檢查目前的組織。',
    unchanged: '連線未變更。選取未完成。',
    superseded: '另一項選取已優先處理。請檢查目前的組織。',
    unavailable: '此已儲存的組織已無法選取。',
    disabled: '組織選取尚未啟用。',
    cancelled: '已取消選取。'
  },
  ar: {
    confirm: 'تأكيد اختيار المؤسسة',
    cancel: 'إلغاء الاختيار',
    close: 'إغلاق',
    explanation: 'انتقل إلى الاتصال وملف التعريف المحفوظين ثم تحقق من المؤسسة. لن يبدأ هذا العمل أو يستأنفه.',
    pending: 'جارٍ اختيار المؤسسة والتحقق منها. بدأ الاختيار ولا يمكن إلغاؤه هنا.',
    verified: 'تم التحقق من المؤسسة المختارة.',
    'committed-unverified': 'تعذر التحقق من المؤسسة المختارة. ربما تغير الاتصال. تحقق من المؤسسة الحالية قبل المتابعة.',
    unchanged: 'لم يتغير الاتصال. لم يكتمل الاختيار.',
    superseded: 'حصل اختيار آخر على الأولوية. تحقق من المؤسسة الحالية.',
    unavailable: 'لم تعد هذه المؤسسة المحفوظة متاحة للاختيار.',
    disabled: 'اختيار المؤسسة غير مفعّل.',
    cancelled: 'تم إلغاء الاختيار.'
  },
  ru: {
    confirm: 'Подтвердить выбор организации',
    cancel: 'Отменить выбор',
    close: 'Закрыть',
    explanation:
      'Перейти к сохранённому подключению и профилю, затем проверить организацию. Работа не начнётся и не возобновится.',
    pending: 'Выбор и проверка организации. Выбор уже начат, отменить его здесь нельзя.',
    verified: 'Выбранная организация проверена.',
    'committed-unverified':
      'Не удалось проверить выбранную организацию. Подключение могло измениться. Проверьте текущую организацию перед продолжением.',
    unchanged: 'Подключение не изменилось. Выбор не завершён.',
    superseded: 'Другой выбор получил приоритет. Проверьте текущую организацию.',
    unavailable: 'Эта сохранённая организация больше недоступна для выбора.',
    disabled: 'Выбор организации не включён.',
    cancelled: 'Выбор отменён.'
  }
}
