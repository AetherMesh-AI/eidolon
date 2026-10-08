import { expect, it } from 'vitest'

import { projectSelectionConfiguration } from './organization-project-selection-config'

it('binds every configured repository to its own exact recipe and root', () => {
  const roots = ['/fixture/frontend', '/fixture/backend']
  const config = projectSelectionConfiguration(roots)

  for (const [index, project] of config.projects.entries()) {
    const recipe = config.project_grants.find(grant => grant.id === project.recipe)!
    expect(recipe.execution.root).toBe(project.root)
    expect(recipe.files.every(file => file.startsWith(`${project.root}/`))).toBe(true)
    expect(config.read_roots[Number(project.root.slice(4))]).toBe(roots[index])
  }

  expect(new Set(config.projects.map(project => project.root)).size).toBe(roots.length)
  expect(() => projectSelectionConfiguration([roots[0]])).toThrow('one root per configured repository')
})
