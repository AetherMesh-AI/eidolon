/** Hold only requested synthetic observation replies, without advancing app time. */
export function createGalleryObservationControl() {
  let paused = false
  const replies: { reply: () => void; current: () => boolean }[] = []

  return {
    get pending() { return replies.filter(item => item.current()).length },
    pause() { paused = true },
    respond(reply: () => void, current: () => boolean = () => true) {
      if (paused) { replies.push({ reply, current }) } else if (current()) { reply() }
    },
    release() {
      paused = false
      const pending = replies.splice(0)
      pending.forEach(item => { if (item.current()) { item.reply() } })
    },
    discard() {
      paused = false
      replies.splice(0)
    }
  }
}
