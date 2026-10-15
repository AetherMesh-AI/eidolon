import type { Locale } from './types'

const messages = {
  heading: ['Project milestones','プロジェクトの節目','项目里程碑','專案里程碑','معالم المشروع','Этапы проекта'],
  open: ['Load milestones','節目を読み込む','加载里程碑','載入里程碑','تحميل المعالم','Загрузить этапы'],
  new: ['New milestone','新しい節目','新建里程碑','新增里程碑','معلم جديد','Новый этап'],
  id: ['Milestone ID','節目の ID','里程碑 ID','里程碑 ID','معرّف المعلم','Идентификатор этапа'],
  title: ['Title','タイトル','标题','標題','العنوان','Название'],
  objectives: ['Linked objectives','関連する目標','关联目标','關聯目標','الأهداف المرتبطة','Связанные цели'],
  dependencies: ['Prerequisite milestones','前提となる節目','前置里程碑','先決里程碑','المعالم السابقة المطلوبة','Предшествующие этапы'],
  save: ['Review and save milestone','確認して節目を保存','确认并保存里程碑','確認並儲存里程碑','مراجعة المعلم وحفظه','Проверить и сохранить этап'],
  retry: ['Retry the same save','同じ保存を再試行','重试相同保存','重試相同儲存','إعادة محاولة الحفظ نفسه','Повторить то же сохранение'],
  confirm: ['I reviewed the linked objectives and prerequisites.','関連する目標と前提条件を確認しました。','我已确认关联目标和前置条件。','我已確認關聯目標及先決條件。','راجعت الأهداف المرتبطة والمتطلبات السابقة.','Я проверил связанные цели и предварительные условия.'],
  note: ['Milestones organize existing work. Saving does not start work or increase spending.','節目は既存の作業を整理します。保存しても作業は開始せず、支出枠も増えません。','里程碑用于组织现有工作。保存不会启动工作或增加支出。','里程碑用於整理現有工作。儲存不會啟動工作或增加支出。','تنظم المعالم العمل الحالي. الحفظ لا يبدأ العمل ولا يزيد الإنفاق.','Этапы упорядочивают текущую работу. Сохранение не запускает работу и не увеличивает расходы.'],
  changed: ['The owner connection changed. Load milestones to open the current organization.','所有者の接続が変わりました。節目を読み込み、現在の組織を開いてください。','所有者连接已变化，请加载里程碑以打开当前组织。','擁有者連線已變更，請載入里程碑以開啟目前組織。','تغير اتصال المالك. حمّل المعالم لفتح المؤسسة الحالية.','Соединение владельца изменилось. Загрузите этапы текущей организации.'],
  reconcile: ['Check pending save receipt', '保留中の保存結果を確認', '检查待处理保存回执', '檢查待處理儲存回執', 'التحقق من إيصال الحفظ المعلق', 'Проверить квитанцию сохранения'],
  returnOriginal: ['Return to the same owner connection and check the pending save receipt before retrying.', '同じ所有者の接続に戻り、再試行する前に保留中の保存結果を確認してください。', '请返回同一所有者连接，在重试之前检查待处理保存回执。', '請返回同一擁有者連線，在重試前檢查待處理儲存回執。', 'عُد إلى اتصال المالك نفسه وتحقق من إيصال الحفظ المعلق قبل إعادة المحاولة.', 'Вернитесь к соединению того же владельца и проверьте квитанцию перед повторной попыткой.'],
  planned: ['Planned','計画済み','已规划','已規劃','مخطط','Запланировано'],
  blocked: ['Waiting for prerequisites','前提条件を待機中','等待前置条件','等待先決條件','بانتظار المتطلبات السابقة','Ожидает предварительных условий'],
  in_progress: ['In progress','進行中','进行中','進行中','قيد التنفيذ','В работе'],
  needs_attention: ['Needs review','確認が必要','需要确认','需要確認','تحتاج إلى مراجعة','Требуется проверка'],
  complete: ['Accepted','承認済み','已验收','已驗收','مقبول','Принято']
}

type Copy = Record<keyof typeof messages, string>
const at = (index: number) => Object.fromEntries(Object.entries(messages).map(([key, values]) => [key, values[index]])) as Copy
export const milestoneCopy: Record<Locale, Copy> = { en: at(0), ja: at(1), zh: at(2), 'zh-hant': at(3), ar: at(4), ru: at(5) }
