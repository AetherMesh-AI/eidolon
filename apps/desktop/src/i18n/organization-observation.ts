import type { Locale } from './types'

const messages = {
  loading: ['Loading organization data', '組織データを読み込み中', '正在加载组织数据', '正在載入組織資料', 'جارٍ تحميل بيانات المؤسسة', 'Загрузка данных организации'],
  unavailable: ['Organization data unavailable', '組織データを取得できません', '组织数据暂不可用', '組織資料暫時無法取得', 'بيانات المؤسسة غير متاحة', 'Данные организации недоступны'],
  unknown: ['The current state has not been received yet.', '現在の状態はまだ受信されていません。', '尚未收到当前状态。', '尚未收到目前狀態。', 'لم يتم استلام الحالة الحالية بعد.', 'Текущее состояние ещё не получено.'],
  stale: ['Showing last-known data. The current state is not confirmed.', '最後に受信したデータを表示しています。現在の状態は未確認です。', '显示最后已知的数据，当前状态尚未确认。', '顯示最後已知的資料，目前狀態尚未確認。', 'تُعرض آخر بيانات معروفة. لم يتم تأكيد الحالة الحالية.', 'Показаны последние известные данные. Текущее состояние не подтверждено.'],
  empty: ['No records in the last received state', '最後に受信した状態に記録はありません', '最后收到的状态中没有记录', '最後收到的狀態中沒有記錄', 'لا توجد سجلات في آخر حالة مستلمة', 'В последнем полученном состоянии нет записей'],
  noMatches: ['No matches in last-known data', '最後に受信したデータに一致する結果はありません', '最后已知的数据中没有匹配项', '最後已知的資料中沒有符合項目', 'لا توجد نتائج مطابقة في آخر بيانات معروفة', 'Нет совпадений в последних известных данных']
} satisfies Record<string, [string, string, string, string, string, string]>

export const observationCopy = Object.fromEntries(
  (['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const).map((locale, index) => [locale,
    Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]]))])
) as Record<Locale, Record<keyof typeof messages, string>>
