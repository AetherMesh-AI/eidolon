import fs from 'node:fs'
import path from 'node:path'

/** Keep startup failures useful without replaying an earlier update's output. */
export function captureUpdaterStartupLog(hermesHome: string) {
  const logPath = path.join(hermesHome, 'logs', 'desktop-update-handoff.log')
  let offset = 0

  try { offset = fs.statSync(logPath).size } catch { /* The first update has no log yet. */ }

  return {
    path: logPath,
    readNewTail(): string {
      let fd: number | undefined

      try {
        fd = fs.openSync(logPath, 'r')
        const size = fs.fstatSync(fd).size
        const start = Math.max(size < offset ? 0 : offset, size - 1200)
        const buffer = Buffer.alloc(size - start)
        const count = fs.readSync(fd, buffer, 0, buffer.length, start)

        return buffer.subarray(0, count).toString('utf8').trim()
      } catch {
        return ''
      } finally {
        if (fd !== undefined) { fs.closeSync(fd) }
      }
    }
  }
}
