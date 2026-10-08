import type { OrganizationProjectBinding, OrganizationProjectSave, OrganizationProjectSetup } from './types'

const identifier = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_-]{0,63}$/.test(value)
const rootAlias = (value: unknown): value is string => typeof value === 'string' && /^root[0-7]$/.test(value)
const teamName = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 64 && value === value.trim()
const unique = (values: string[]) => new Set(values).size === values.length

export function sameProjectBinding(left: OrganizationProjectBinding, right: OrganizationProjectBinding) {
  return ['id', 'root', 'recipe', 'team'].every(key => left[key as keyof OrganizationProjectBinding] === right[key as keyof OrganizationProjectBinding])
}

const validProjects = (projects: unknown): projects is OrganizationProjectBinding[] => Array.isArray(projects) && projects.length <= 8 && projects.every(project => project && identifier(project.id) && rootAlias(project.root) && identifier(project.recipe) && teamName(project.team)) && unique(projects.map(project => project.id)) && unique(projects.map(project => project.root))

export function validProjectSave(value: unknown, project: OrganizationProjectBinding): value is OrganizationProjectSave {
  if (!value || typeof value !== 'object') {return false}
  const result = value as OrganizationProjectSave

  return result.version === 1 && result.saved === true && typeof result.revision === 'string' && /^[a-f0-9]{64}$/.test(result.revision) && !!result.project && sameProjectBinding(result.project, project)
}

export function validProjectSetup(value: unknown): value is OrganizationProjectSetup {
  if (!value || typeof value !== 'object') {return false}
  const setup = value as OrganizationProjectSetup

  return setup.version === 1 && typeof setup.revision === 'string' && /^[a-f0-9]{64}$/.test(setup.revision) &&
    Array.isArray(setup.roots) && setup.roots.length <= 8 && setup.roots.every(rootAlias) && unique(setup.roots) &&
    Array.isArray(setup.teams) && setup.teams.length <= 65 && setup.teams.every(teamName) && unique(setup.teams) &&
    Array.isArray(setup.recipes) && setup.recipes.length <= 8 && setup.recipes.every(recipe => recipe && identifier(recipe.id) && rootAlias(recipe.root)) && unique(setup.recipes.map(recipe => recipe.id)) &&
    validProjects(setup.projects) &&
    (setup.storage === undefined || (setup.storage === 'profile-ledger' && setup.ledgerProjects !== undefined)) &&
    (setup.ledgerProjects === undefined || validProjects(setup.ledgerProjects)) &&
    (setup.registryConflicts === undefined || (Array.isArray(setup.registryConflicts) && setup.registryConflicts.length <= 32 && setup.registryConflicts.every(reason => typeof reason === 'string' && reason.length <= 512))) &&
    (setup.repair === undefined || setup.repair === null || (typeof setup.repair === 'string' && setup.repair.length <= 2048)) &&
    typeof setup.blocked === 'boolean' && Array.isArray(setup.blockers) && setup.blockers.every(code => typeof code === 'string')
}

export function validProjectBinding(project: OrganizationProjectBinding, setup: OrganizationProjectSetup) {
  return !setup.blocked && setup.projects.length < 8 && !setup.projects.some(item => item.root === project.root) && identifier(project.id) && !setup.projects.some(item => item.id === project.id) &&
    setup.roots.includes(project.root) && setup.recipes.some(recipe => recipe.id === project.recipe && recipe.root === project.root) && setup.teams.includes(project.team)
}
