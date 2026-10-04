export function AgentAvatar({ name }: { name: string }) {
 return <span aria-label={`${name} avatar`} className="eid-avatar" role="img">{name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase() || '?'}</span>
}
