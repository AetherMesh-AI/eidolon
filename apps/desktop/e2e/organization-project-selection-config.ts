/** Owner configuration for the native selection scenario; no tool grants are added. */
export function projectSelectionConfiguration(roots: string[]) {
  const ids = ['frontend', 'backend']

  if (roots.length !== ids.length) {
    throw new Error('Project selection requires one root per configured repository')
  }

  return {
    read_roots: roots,
    project_grants: ids.map((id, index) => ({
      id: `${id}-tests`,
      files: [`root${index}/test_app.py`],
      execution: { root: `root${index}` }
    })),
    projects: ids.map((id, index) => ({ id, root: `root${index}`, recipe: `${id}-tests`, team: id }))
  }
}
