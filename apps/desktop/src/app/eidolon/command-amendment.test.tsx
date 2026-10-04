import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it } from 'vitest'

import { createPrototypeAdapter } from './adapter'
import { OrganizationWorkspace } from './workspace'
afterEach(cleanup)
it.each(['P5', 'P4', 'P3', 'P2', 'P1'])('stores %s without exposing numeric priority ranks', priority => {
 const adapter = createPrototypeAdapter()
 render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
 expect(screen.queryByLabelText('Summary')).toBeNull()
 expect(screen.queryByRole('slider')).toBeNull()
 fireEvent.click(screen.getByRole('checkbox', {name: 'Priority'}))
 const slider=screen.getByRole('slider', {name: 'Priority level'})
 const endpoints = slider.parentElement!.querySelector('.eid-priority-endpoints')!
 expect(endpoints).not.toBeNull()
 expect(Array.from(endpoints.children, child => child.textContent)).toEqual(['Lowest', 'Highest'])
 expect(screen.queryByText(/Lowest · Low/)).toBeNull()
 fireEvent.change(slider, {target:{value: ['P5','P4','P3','P2','P1'].indexOf(priority)}})
 expect(slider.getAttribute('aria-valuetext')).toBe(['Lowest','Low','Normal','High','Highest'][['P5','P4','P3','P2','P1'].indexOf(priority)])
 expect(screen.queryByText(/P[1-5]/)).toBeNull()
 fireEvent.change(screen.getByRole('textbox', {name:'Objective'}), {target:{value:'Bounded command'}})
 fireEvent.click(screen.getByRole('button', {name:/Create objective/}))
 expect(adapter.getSnapshot().objectives[0].priority).toBe(priority)
})
