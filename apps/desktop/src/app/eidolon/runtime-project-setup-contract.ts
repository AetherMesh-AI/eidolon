import type { OrganizationProjectBinding, OrganizationProjectSetup } from './types'

const identifier = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(value)
const rootAlias = (value: unknown): value is string => typeof value === 'string' && /^root[0-7]$/.test(value)
const teamName = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 64 && value === value.trim()
const unique = (values: string[]) => new Set(values).size === values.length

export function validProjectSetup(value: unknown): value is OrganizationProjectSetup {
  if (!value || typeof value !== 'object') {return false}
  const setup = value as OrganizationProjectSetup

  return setup.version === 1 && typeof setup.revision === 'string' && /^[a-f0-9]{64}$/.test(setup.revision) &&
    Array.isArray(setup.roots) && setup.roots.length <= 8 && setup.roots.every(rootAlias) && unique(setup.roots) &&
    Array.isArray(setup.teams) && setup.teams.length <= 65 && setup.teams.every(teamName) && unique(setup.teams) &&
    Array.isArray(setup.recipes) && setup.recipes.length <= 8 && setup.recipes.every(recipe => recipe && identifier(recipe.id) && rootAlias(recipe.root)) && unique(setup.recipes.map(recipe => recipe.id)) &&
    Array.isArray(setup.projects) && setup.projects.length <= 8 && setup.projects.every(project => project && identifier(project.id) && rootAlias(project.root) && identifier(project.recipe) && teamName(project.team)) && unique(setup.projects.map(project => project.id)) && unique(setup.projects.map(project => project.root)) &&
    typeof setup.blocked === 'boolean' && Array.isArray(setup.blockers) && setup.blockers.every(code => typeof code === 'string')
}

export function validProjectBinding(project: OrganizationProjectBinding, setup: OrganizationProjectSetup) {
  return !setup.blocked && setup.projects.length < 8 && !setup.projects.some(item => item.root === project.root) && identifier(project.id) && !setup.projects.some(item => item.id === project.id) &&
    setup.roots.includes(project.root) && setup.recipes.some(recipe => recipe.id === project.recipe && recipe.root === project.root) && setup.teams.includes(project.team)
}
