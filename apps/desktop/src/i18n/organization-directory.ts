import type { OrganizationDirectoryCopy } from '@/app/eidolon/organization-directory-search'

import type { Locale } from './types'

export const organizationDirectoryCopy: Record<
  Locale,
  OrganizationDirectoryCopy & { choose: string; inspectionOnly: string }
> = {
  en: {
    heading: 'Organizations',
    description: 'Inspect names verified in this window. Refresh reads only the current organization.',
    search: 'Search organizations',
    refresh: 'Refresh current organization',
    empty: 'No verified organizations match',
    lastKnown: 'Last known',
    verified: 'Verified now',
    unavailable: 'Current organization unavailable',
    unnamed: 'Unnamed organization',
    unconfigured: 'Not configured',
    unsupported: 'Unavailable in this runtime',
    choose: 'Use selected organization',
    inspectionOnly: 'Organization switching is not available here yet.'
  },
  ja: {
    heading: '組織一覧',
    description: 'このウィンドウで確認済みの名前を表示します。更新は現在の組織のみを読み取ります。',
    search: '組織を検索',
    refresh: '現在の組織を更新',
    empty: '一致する確認済みの組織はありません',
    lastKnown: '前回の情報',
    verified: '確認済み',
    unavailable: '現在の組織を利用できません',
    unnamed: '名前のない組織',
    unconfigured: '未設定',
    unsupported: 'このランタイムでは利用できません',
    choose: '選択した組織を使用',
    inspectionOnly: 'ここでは組織を切り替えられません。'
  },
  zh: {
    heading: '组织列表',
    description: '查看此窗口已验证的名称。刷新仅读取当前组织。',
    search: '搜索组织',
    refresh: '刷新当前组织',
    empty: '没有匹配的已验证组织',
    lastKnown: '上次的信息',
    verified: '已验证',
    unavailable: '当前组织不可用',
    unnamed: '未命名组织',
    unconfigured: '未配置',
    unsupported: '此运行时不支持',
    choose: '使用所选组织',
    inspectionOnly: '此处暂不支持切换组织。'
  },
  'zh-hant': {
    heading: '組織清單',
    description: '查看此視窗已驗證的名稱。重新整理僅讀取目前組織。',
    search: '搜尋組織',
    refresh: '重新整理目前組織',
    empty: '沒有相符的已驗證組織',
    lastKnown: '上次的資訊',
    verified: '已驗證',
    unavailable: '目前組織無法使用',
    unnamed: '未命名組織',
    unconfigured: '尚未設定',
    unsupported: '此執行階段不支援',
    choose: '使用所選組織',
    inspectionOnly: '此處暫不支援切換組織。'
  },
  ar: {
    heading: 'المؤسسات',
    description: 'اعرض الأسماء التي تم التحقق منها في هذه النافذة. يقرأ التحديث المؤسسة الحالية فقط.',
    search: 'البحث عن المؤسسات',
    refresh: 'تحديث المؤسسة الحالية',
    empty: 'لا توجد مؤسسات متحقق منها مطابقة',
    lastKnown: 'آخر معلومات معروفة',
    verified: 'تم التحقق الآن',
    unavailable: 'المؤسسة الحالية غير متاحة',
    unnamed: 'مؤسسة بلا اسم',
    unconfigured: 'غير مهيأة',
    unsupported: 'غير متاحة في هذا التشغيل',
    choose: 'استخدام المؤسسة المحددة',
    inspectionOnly: 'تبديل المؤسسات غير متاح هنا بعد.'
  },
  ru: {
    heading: 'Организации',
    description: 'Имена, проверенные в этом окне. Обновление читает только текущую организацию.',
    search: 'Поиск организаций',
    refresh: 'Обновить текущую организацию',
    empty: 'Нет подходящих проверенных организаций',
    lastKnown: 'Последние известные данные',
    verified: 'Проверено сейчас',
    unavailable: 'Текущая организация недоступна',
    unnamed: 'Организация без названия',
    unconfigured: 'Не настроено',
    unsupported: 'Недоступно в этой среде',
    choose: 'Использовать выбранную организацию',
    inspectionOnly: 'Переключение организаций здесь пока недоступно.'
  }
}
