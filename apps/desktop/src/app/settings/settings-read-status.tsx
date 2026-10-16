import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

export function SettingsReadStatus({
  failed,
  hasData,
  incomplete = false,
  loading,
  onRefresh,
  title
}: {
  failed: boolean
  hasData: boolean
  incomplete?: boolean
  loading: boolean
  onRefresh: () => Promise<void>
  title: string
}) {
  const { t } = useI18n()

  return (
    <div
      className="mb-4 flex flex-wrap items-center justify-end gap-3"
      data-settings-read-state={failed ? (hasData ? 'stale' : 'error') : loading ? 'refreshing' : 'ready'}
    >
      {failed && (
        <div className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]" role="alert">
          <p>{title}</p>
          {(hasData || incomplete) && (
            <p className="mt-1 text-muted-foreground">
              {incomplete ? t.settings.readState.incomplete : t.settings.readState.stale}
            </p>
          )}
        </div>
      )}
      <Button aria-busy={loading} disabled={loading} onClick={() => void onRefresh()} size="sm" variant="outline">
        {loading && <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />}
        {t.common.refresh}
      </Button>
    </div>
  )
}
