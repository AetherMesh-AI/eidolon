import type { Locale } from './types'

const messages = {
  heading: ['Outcome requirements', '成果の要件', '成果要求', '成果要求', 'متطلبات النتيجة', 'Требования к результату'],
  deliverable: ['Reviewed deliverable', 'レビュー済み成果物', '已审查交付物', '已審查交付物', 'مخرج تمت مراجعته', 'Проверенный результат'],
  authorized_check: ['Verified check', '検証済みチェック', '已验证检查', '已驗證檢查', 'فحص موثّق', 'Подтверждённая проверка'],
  source_integration: ['Verified source integration', '検証済みソース統合', '已验证源码集成', '已驗證原始碼整合', 'دمج مصدر موثّق', 'Подтверждённая интеграция исходников'],
  human_judgment: ['My explicit review', '自分による明示的なレビュー', '我的明确审查', '我的明確審查', 'مراجعتي الصريحة', 'Моё явное одобрение'],
  revision: ['Revision', '改訂', '修订', '修訂', 'المراجعة', 'Редакция'],
  reason: ['Review reason', 'レビューの理由', '审查理由', '審查理由', 'سبب القرار', 'Причина решения'],
  approve: ['Approve this requirement', 'この要件を承認', '批准此要求', '批准此要求', 'الموافقة على هذا المتطلب', 'Одобрить требование'],
  reject: ['Reject this requirement', 'この要件を却下', '拒绝此要求', '拒絕此要求', 'رفض هذا المتطلب', 'Отклонить требование'],
  confirm: ['I reviewed this exact deliverable and requirement.', 'この成果物と要件を確認しました。', '我已审查此确切交付物和要求。', '我已審查此確切交付物和要求。', 'راجعت هذا المخرج والمتطلب المحددين.', 'Я проверил именно этот результат и требование.'],
  saved: ['Review recorded. Final acceptance still requires all checks.', 'レビューを記録しました。最終承認には全チェックが必要です。', '审查已记录。最终验收仍需通过所有检查。', '審查已記錄。最終驗收仍需通過所有檢查。', 'تم تسجيل المراجعة. لا يزال القبول النهائي يتطلب كل الفحوص.', 'Решение записано. Итоговая приёмка требует всех проверок.'],
  unavailable: ['Reconnect to the owning organization to review this outcome.', '所有組織に再接続して成果をレビューしてください。', '重新连接所属组织以审查此成果。', '重新連線所屬組織以審查此成果。', 'أعد الاتصال بالمؤسسة المالكة لمراجعة النتيجة.', 'Подключитесь к организации-владельцу для проверки результата.'],
  retry: ['Retry exact review', '同じレビューを再試行', '重试相同审查', '重試相同審查', 'إعادة نفس المراجعة', 'Повторить то же решение'],
  checks: ['Choose a check', 'チェックを選択', '选择检查', '選擇檢查', 'اختر فحصًا', 'Выберите проверку'],
  project_tests: ['Project tests', 'プロジェクトテスト', '项目测试', '專案測試', 'اختبارات المشروع', 'Тесты проекта'],
  managed_validation: ['Managed artifact validation', '管理成果物の検証', '托管交付物验证', '受管交付物驗證', 'التحقق من المخرج المُدار', 'Проверка управляемого артефакта'],
  note: ['Review requirements do not authorize tools or change project permissions.', 'レビュー要件はツールやプロジェクト権限を許可しません。', '审查要求不会授权工具或更改项目权限。', '審查要求不會授權工具或變更專案權限。', 'متطلبات المراجعة لا تمنح صلاحيات أدوات أو تغيّر أذونات المشروع.', 'Требования проверки не дают разрешений инструментам и не меняют доступ к проекту.']
} as const

type Copy = { [K in keyof typeof messages]: string }
const get = (i: number): Copy => Object.fromEntries(Object.entries(messages).map(([k,v]) => [k,v[i]])) as Copy
export const outcomeContractCopy: Record<Locale, Copy> = { en: get(0), ja: get(1), zh: get(2), 'zh-hant': get(3), ar: get(4), ru: get(5) }
