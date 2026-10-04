import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { Inspector } from '@/app/eidolon/inspector'
import { $backgroundStatusBySession } from '@/store/composer-status'

import { TerminalSlot } from './persistent'
import { TerminalRail } from './rail'
import { $activeTerminal, $terminals } from './terminals'

/** Pane-side terminal chrome: the body slot (which the persistent overlay chases)
 *  plus the always-on tab rail. Lives in the real pane DOM — NOT the z-4 terminal
 *  overlay — so the rail sits above the collapsed sidebars' z-30 hover-reveal
 *  triggers (z-40, like the thread timeline) and suppresses them while hovered.
 *  The rail is always shown when a terminal exists (even one), so every tab keeps
 *  its close affordance; closing the last one hides the pane (reopen re-creates). */
export function TerminalPaneChrome() {
  const terminals = useStore($terminals)
  const activeTerminal = useStore($activeTerminal)
  const [inspectedId, setInspectedId] = useState<string | null>(null)
  const [kind, setKind] = useState<'terminal' | 'process'>('terminal')
  const background = useStore($backgroundStatusBySession)

  const matches = Object.entries(background).flatMap(([sessionId, items]) => items
    .filter(item => item.type === 'background' && item.id === activeTerminal?.procId)
    .map(item => ({ sessionId, item })))

  // Never guess ownership if a process handle is absent or ambiguous.
  const process = matches.length === 1 ? matches[0] : undefined

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {activeTerminal && <div className="eidolon relative z-40 flex max-h-96 shrink-0 flex-col gap-2 overflow-auto p-2">
          <button className="eid-button" onClick={() => { setKind('terminal'); setInspectedId(activeTerminal.id) }} type="button">Inspect terminal</button>
          {activeTerminal.kind === 'agent' && activeTerminal.procId && <button className="eid-button" onClick={() => { setKind('process'); setInspectedId(activeTerminal.id) }} type="button">Inspect process</button>}
          {inspectedId === activeTerminal.id && kind === 'process' && <Inspector key="process" kind="process" onClose={() => setInspectedId(null)} title={process?.item.title || activeTerminal.title}>
            <dl><dt>Process handle</dt><dd>{activeTerminal.procId}</dd><dt>Runtime owner session</dt><dd>{process?.sessionId || 'Not reported'}</dd><dt>Last reported state</dt><dd>{process?.item.state || 'Unavailable'}</dd><dt>Exit code</dt><dd>{process?.item.exitCode ?? 'Not reported'}</dd></dl>
            <pre className="whitespace-pre-wrap break-words">{process?.item.output || 'No captured output available.'}</pre>
            <p>Read-only runtime snapshot. Missing or ambiguous registry entries do not imply completion. No process is started, stopped, or resumed here.</p>
          </Inspector>}
          {inspectedId === activeTerminal.id && kind === 'terminal' && <Inspector key="terminal" kind="terminal" onClose={() => setInspectedId(null)} title={activeTerminal.title}>
            <dl><dt>Terminal handle</dt><dd>{activeTerminal.id}</dd><dt>Mode</dt><dd>{activeTerminal.kind === 'agent' ? 'Read-only agent process mirror' : 'Interactive user shell'}</dd><dt>Working directory</dt><dd>{activeTerminal.restoreCwd || activeTerminal.cwd || 'Not reported'}</dd></dl>
            <p>The terminal handle is not an operating-system PID. Inspection does not start or resume a process.</p>
          </Inspector>}
        </div>}
        <TerminalSlot />
      </div>
      {terminals.length > 0 && <TerminalRail />}
    </div>
  )
}
