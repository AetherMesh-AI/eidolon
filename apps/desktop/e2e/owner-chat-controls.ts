import { expect, type Locator } from '@playwright/test'

/** Exercise the actual keyboard disclosure; inline agent inspectors have none. */
export async function setOwnerChatDetails(conversation: Locator, open: boolean) {
  const details = conversation.locator('.eid-owner-chat-compose-details')

  if (!await details.count()) {
    return
  }

  const isOpen = await details.getAttribute('open') !== null

  if (isOpen !== open) {
    await details.locator('summary').press('Enter')
  }

  if (open) {
    await expect(details).toHaveAttribute('open', '')
    await expect(details.locator('.eid-owner-chat-allowance')).toBeVisible()
  } else {
    await expect(details).not.toHaveAttribute('open', '')
  }
}
