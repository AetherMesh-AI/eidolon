/** Independently reviewed native labels; never imported from production translations. */
export const sharedPolicyCopy = {
  en: {
    title: 'Shared installation allowance', edit: 'Configure shared allowance', observe: 'Record shared remaining percent',
    label: 'Period identifier', startsAt: 'Period starts (UTC)', endsAt: 'Period ends (UTC)',
    limits: 'Aggregate pool limits', protected: 'Protected completion reserve', calls: 'Model calls', tokens: 'Total tokens',
    cost: 'Configured cost (USD, optional)', enroll: 'Enroll organization', allocation: 'This organization’s allocation',
    saved: 'Change verified and saved.'
  },
  ja: {
    title: 'インストール全体の共有利用枠', edit: '共有利用枠を設定', observe: '共有残量率を記録',
    label: '期間の識別子', startsAt: '期間開始（UTC）', endsAt: '期間終了（UTC）',
    limits: '共有枠の合計上限', protected: '保護された完了用予約枠', calls: 'モデル呼び出し数', tokens: '合計トークン数',
    cost: '設定費用（USD、任意）', enroll: '組織を登録', allocation: 'この組織の配分',
    saved: '変更を検証し、保存しました。'
  },
  zh: {
    title: '安装级共享额度', edit: '配置共享额度', observe: '记录共享剩余百分比',
    label: '周期标识', startsAt: '周期开始（UTC）', endsAt: '周期结束（UTC）',
    limits: '共享池总限额', protected: '受保护的完成预留', calls: '模型调用次数', tokens: '令牌总数',
    cost: '配置成本（美元，可选）', enroll: '加入组织', allocation: '此组织的分配额度',
    saved: '更改已验证并保存。'
  },
  'zh-hant': {
    title: '安裝層級共用額度', edit: '設定共用額度', observe: '記錄共用剩餘百分比',
    label: '週期識別碼', startsAt: '週期開始（UTC）', endsAt: '週期結束（UTC）',
    limits: '共用額度總上限', protected: '受保護的完成預留', calls: '模型呼叫次數', tokens: '權杖總數',
    cost: '設定成本（美元，選填）', enroll: '加入組織', allocation: '此組織的分配額度',
    saved: '變更已驗證並儲存。'
  },
  ar: {
    title: 'الحصة المشتركة للتثبيت', edit: 'ضبط الحصة المشتركة', observe: 'تسجيل النسبة المتبقية المشتركة',
    label: 'معرّف الفترة', startsAt: 'بداية الفترة (UTC)', endsAt: 'نهاية الفترة (UTC)',
    limits: 'الحدود الإجمالية للحصة', protected: 'احتياطي الإكمال المحمي', calls: 'استدعاءات النموذج', tokens: 'إجمالي الرموز',
    cost: 'التكلفة المحددة (USD، اختياري)', enroll: 'تسجيل المؤسسة', allocation: 'تخصيص هذه المؤسسة',
    saved: 'تم التحقق من التغيير وحفظه.'
  },
  ru: {
    title: 'Общий лимит установки', edit: 'Настроить общий лимит', observe: 'Записать общий оставшийся процент',
    label: 'Идентификатор периода', startsAt: 'Начало периода (UTC)', endsAt: 'Конец периода (UTC)',
    limits: 'Совокупные лимиты', protected: 'Защищённый резерв завершения', calls: 'Вызовы модели', tokens: 'Всего токенов',
    cost: 'Расчётная стоимость (USD, необязательно)', enroll: 'Включить организацию', allocation: 'Выделение этой организации',
    saved: 'Изменение проверено и сохранено.'
  }
} as const

export type SharedPolicyLocale = keyof typeof sharedPolicyCopy
