// The generic diagnostics action is local-only until Eidolon configures its
// own support service. Keep copying available without implying an upload.
import { useStore } from '@nanostores/react'

import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { $sendDiagnostics, dismissSendDiagnostics } from '@/store/send-diagnostics'

export function SendDiagnosticsHost() {
  const { t } = useI18n()
  const copy = t.sendDiagnostics
  const state = useStore($sendDiagnostics)

  if (!state) {
    return null
  }

  return (
    <Dialog onOpenChange={open => (!open ? dismissSendDiagnostics() : undefined)} open>
      <DialogContent className="max-w-[30rem]">
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription className="whitespace-pre-line text-left">{copy.privacyNotice}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          {state.errorContext && (
            <CopyButton label={t.assistant.thread.errorCopyDiagnostics} text={state.errorContext} />
          )}
          <Button onClick={dismissSendDiagnostics} variant="ghost">
            {copy.close}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
