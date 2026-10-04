import './eidolon.css'

// Read/export-only historical entry. It never imports the gateway or writable
// prototype harness and never migrates saved examples into dispatched work.
import { createRoot } from 'react-dom/client'

import { LegacyOrganizationHistory } from './legacy-history'

createRoot(document.getElementById('root')!).render(<LegacyOrganizationHistory />)
