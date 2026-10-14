import './workspace-page-heading.css'

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface WorkspacePageHeadingProps {
  title: string
  description?: ReactNode
  children?: ReactNode
  className?: string
}

/** A page title, separate from the window shell and each page's action owners. */
export function WorkspacePageHeading({ title, description, children, className }: WorkspacePageHeadingProps) {
  return <header className={cn('eid-workspace-page-heading', className)}>
    <h1>{title}</h1>
    {description && <p>{description}</p>}
    {children}
  </header>
}
