import { useI18n } from '@/i18n/context'

/** One locale-aware renderer for retained timestamps. Never rewrite source records. */
export function LocalizedTime({ value }: { value: string | number }) {
  const { locale, t } = useI18n()
  const date = new Date(value)

  return Number.isFinite(date.getTime())
    ? <time dateTime={typeof value === 'string' ? value : date.toISOString()}>{date.toLocaleString(locale)}</time>
    : <span>{t.organizationFoundation.notRecorded}</span>
}
