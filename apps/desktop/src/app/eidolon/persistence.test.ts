import { expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'
it('persists local objectives across adapter restart without runtime execution', () => {
 const data = new Map<string,string>()
 const storage = { getItem: (key:string) => data.get(key) ?? null, setItem: (key:string,value:string) => { data.set(key,value) } }
 const first = createPrototypeAdapter(storage)
 const objective = first.createObjective('Persist isolated objective', { priority:'P1' })
 const next = createPrototypeAdapter(storage)
 expect(next.getSnapshot().objectives[0]).toEqual(objective)
 next.reset()
 expect(createPrototypeAdapter(storage).getSnapshot().objectives).toEqual([])
})
