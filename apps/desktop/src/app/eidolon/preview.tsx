import './eidolon.css'

// Isolated browser QA entry: deliberately imports no Electron or gateway modules.
import { createRoot } from 'react-dom/client'
import { HashRouter, Navigate, Route, Routes } from 'react-router'

import { OrganizationRail } from './rail'
import { OrganizationWorkspace } from './workspace'

createRoot(document.getElementById('root')!).render(<HashRouter><div className="eidolon" style={{ display: 'grid', gridTemplateColumns: '240px minmax(0, 1fr)', height: '100vh' }}><OrganizationRail sessions={<p>Runtime sessions are intentionally unavailable in this isolated preview.</p>} /><Routes><Route element={<Navigate replace to="/home" />} path="/" />{['/home', '/objectives', '/objectives/:id', '/organization', '/activity', '/knowledge'].map(path => <Route element={<OrganizationWorkspace />} key={path} path={path} />)}<Route element={<main className="eid-page"><h1>Runtime surface</h1><p>This destination uses the existing Eidolon runtime in the desktop application. It is intentionally not connected in this isolated preview.</p></main>} path="*" /></Routes></div></HashRouter>)
