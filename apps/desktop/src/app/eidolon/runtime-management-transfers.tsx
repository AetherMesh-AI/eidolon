import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { useI18n } from '@/i18n/context'

import { transferObjectives, transferTasks } from './runtime-management'
import type { OrganizationConfiguration, OrganizationSnapshot, OrganizationTransfer } from './types'

export function OrganizationTransferFields({
  configuration,
  snapshot,
  disabled,
  onChange
}: {
  configuration: OrganizationConfiguration
  snapshot: OrganizationSnapshot
  disabled: boolean
  onChange(transfers: OrganizationTransfer[]): void
}) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const transfers = configuration.transfers ?? []

  const members = [
    ...new Map(
      [...snapshot.agents, ...configuration.roster]
        .filter(agent => ['Executive', 'Manager', 'Worker'].includes(agent.role))
        .map(agent => [agent.id, agent])
    ).values()
  ]

  const update = (index: number, values: Partial<OrganizationTransfer>) =>
    onChange(transfers.map((transfer, itemIndex) => {
      if (itemIndex !== index) {
        return transfer
      }

      const next = { ...transfer, ...values }

      // Older runtimes reject unknown transfer fields, even when empty.
      if (!next.objectiveIds?.length) {
        delete next.objectiveIds
      }

      return next
    }))

  return (
    <fieldset className="eid-management-fields" disabled={disabled}>
      <legend>{t.organizationWork.transfers}</legend>
      <p className="eid-note">{copy.transferNote}</p>
      {transfers.map((transfer, index) => {
        const source = members.find(agent => agent.id === transfer.fromAgentId)
        const tasks = transferTasks(snapshot, transfer.fromAgentId)
        const objectives = transferObjectives(snapshot, transfer.fromAgentId, source?.role)
        const objectiveIds = transfer.objectiveIds ?? []

        return (
          <fieldset className="eid-management-fields" key={index}>
            <label>
              {copy.transferFrom}
              <select
                className={controlVariants()}
                onChange={event =>
                  update(index, { fromAgentId: event.target.value, toAgentId: '', taskIds: [], objectiveIds: [] })
                }
                value={transfer.fromAgentId}
              >
                <option value="">{t.organizationWork.notRecorded}</option>
                {members.map(agent => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {copy.transferTo}
              <select
                className={controlVariants()}
                onChange={event => update(index, { toAgentId: event.target.value })}
                value={transfer.toAgentId}
              >
                <option value="">{t.organizationWork.notRecorded}</option>
                {members
                  .filter(agent => agent.id !== transfer.fromAgentId && agent.role === source?.role)
                  .map(agent => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name}
                    </option>
                  ))}
              </select>
            </label>
            {(source?.role === 'Manager' || source?.role === 'Executive') && (
              <fieldset>
                <legend>{copy.transferObjectives}</legend>
                <p className="eid-note">{copy.transferObjectivesNote}</p>
                {objectives.length ? (
                  objectives.map(objective => (
                    <label className="eid-inline" key={objective.id}>
                      <input
                        checked={objectiveIds.includes(objective.id)}
                        onChange={event =>
                          update(index, {
                            objectiveIds: event.target.checked
                              ? [...objectiveIds, objective.id]
                              : objectiveIds.filter(id => id !== objective.id)
                          })
                        }
                        type="checkbox"
                      />
                      {objective.title} · {objective.id}
                    </label>
                  ))
                ) : (
                  <p>{copy.noTransferObjectives}</p>
                )}
              </fieldset>
            )}
            <fieldset>
              <legend>{copy.transferTasks}</legend>
              {tasks.length ? (
                tasks.map(task => (
                  <label className="eid-inline" key={task.id}>
                    <input
                      checked={transfer.taskIds.includes(task.id)}
                      onChange={event =>
                        update(index, {
                          taskIds: event.target.checked
                            ? [...transfer.taskIds, task.id]
                            : transfer.taskIds.filter(id => id !== task.id)
                        })
                      }
                      type="checkbox"
                    />
                    {task.title} · {task.status}
                  </label>
                ))
              ) : (
                <p>{copy.noTransferTasks}</p>
              )}
            </fieldset>
            <label className="eid-inline">
              <input
                checked={transfer.includeMemory}
                onChange={event => update(index, { includeMemory: event.target.checked })}
                type="checkbox"
              />
              {t.organizationWork.includeMemory}
            </label>
            <Button
              onClick={() => onChange(transfers.filter((_, itemIndex) => itemIndex !== index))}
              size="sm"
              type="button"
              variant="text"
            >
              {copy.removeTransfer}
            </Button>
          </fieldset>
        )
      })}
      <Button
        disabled={transfers.length >= 64}
        onClick={() => onChange([...transfers, { fromAgentId: '', toAgentId: '', taskIds: [], includeMemory: false }])}
        size="sm"
        type="button"
        variant="secondary"
      >
        {copy.addTransfer}
      </Button>
    </fieldset>
  )
}
