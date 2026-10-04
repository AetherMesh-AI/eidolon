import { OrganizationRuntimeProvider, useRuntimeOrganization } from './runtime-provider'
import { OrganizationWorkspaceView } from './workspace'

export default function RuntimeOrganizationWorkspace() {
  const runtime = useRuntimeOrganization()

  return runtime ? <OrganizationWorkspaceView adapter={runtime.adapter} /> : <OrganizationRuntimeProvider><RuntimeOrganizationWorkspace /></OrganizationRuntimeProvider>
}
