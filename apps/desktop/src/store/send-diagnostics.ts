// Eidolon has no diagnostics upload service configured. This generic app
// action keeps the error context local so it can be copied for manual review.
// Provider-specific support and authentication remain separate workflows.
import { atom } from 'nanostores'

export interface SendDiagnosticsState {
  errorContext?: string
}

export const $sendDiagnostics = atom<SendDiagnosticsState | null>(null)

/** Open the local diagnostics notice. Does not collect logs or upload data. */
export function requestSendDiagnostics(errorContext?: string): void {
  $sendDiagnostics.set({ errorContext })
}

export function dismissSendDiagnostics(): void {
  $sendDiagnostics.set(null)
}
