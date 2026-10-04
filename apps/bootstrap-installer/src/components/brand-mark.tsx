import { cn } from '../lib/utils'

import eidolonIcon from '../../src-tauri/icons/icon.png'

// Use the same Eidolon artwork as the desktop application.
// Ported from apps/desktop's BrandMark; the bundler versions this asset for every release.
export function BrandMark({ className, ...props }: React.ComponentProps<'span'>) {
  return (
    <span className={cn('inline-flex size-14 shrink-0 items-center justify-center bg-white', className)} {...props}>
      <img alt="" className="size-full object-contain" src={eidolonIcon} />
    </span>
  )
}
