import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  activateCustomEndpoint,
  deleteCustomEndpoint,
  getCustomEndpoints,
  saveCustomEndpoint,
  validateCustomEndpoint
} from '@/eidolon'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { Check, Globe, Loader2, Plus, Save, Trash2, Zap } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { confirm } from '@/store/confirm'
import { notify, notifyError } from '@/store/notifications'
import type { CustomEndpoint, CustomEndpointUpdate } from '@/types/eidolon'

import { EmptyState, Pill, SectionHeading, SettingsContent, SettingsSkeleton } from './primitives'
import { SettingsReadStatus } from './settings-read-status'
import { useSettingsRead } from './use-settings-read'

interface CustomEndpointsSettingsProps {
  onConfigSaved?: () => void
  onMainModelChanged?: (provider: string, model: string) => void
}

interface EndpointForm {
  apiKey: string
  baseUrl: string
  contextLength: string
  discoverModels: boolean
  id: string
  makeDefault: boolean
  model: string
  name: string
}

const EMPTY_FORM: EndpointForm = {
  apiKey: '',
  baseUrl: '',
  contextLength: '',
  discoverModels: true,
  id: '',
  makeDefault: true,
  model: '',
  name: ''
}

function formFromEndpoint(endpoint: CustomEndpoint): EndpointForm {
  return {
    apiKey: '',
    baseUrl: endpoint.base_url,
    contextLength: endpoint.context_length ? String(endpoint.context_length) : '',
    discoverModels: endpoint.discover_models,
    id: endpoint.id,
    makeDefault: Boolean(endpoint.is_current),
    model: endpoint.model,
    name: endpoint.name
  }
}

function toPayload(form: EndpointForm, models?: string[]): CustomEndpointUpdate {
  const contextLength = Number.parseInt(form.contextLength, 10)

  return {
    id: form.id.trim() || undefined,
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    model: form.model.trim(),
    api_key: form.apiKey.trim() || undefined,
    context_length: Number.isFinite(contextLength) && contextLength > 0 ? contextLength : undefined,
    discover_models: form.discoverModels,
    make_default: form.makeDefault,
    models: models?.length ? models : undefined
  }
}

// These helpers intentionally follow the active profile, not the Settings override.
const readEndpoints = async () => (await getCustomEndpoints()).endpoints

export function CustomEndpointsSettings({ onConfigSaved, onMainModelChanged }: CustomEndpointsSettingsProps) {
  const { t } = useI18n()
  const m = t.customEndpoints
  const read = useSettingsRead(readEndpoints)
  const { data: endpoints, setData: setEndpoints } = read
  const initializedOwner = useRef(read.owner)
  const initialized = useRef(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [activating, setActivating] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [form, setForm] = useState<EndpointForm>(EMPTY_FORM)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])

  // eslint-disable-next-line no-restricted-syntax -- local draft initialization token; routing is checked synchronously by the read hook
  useEffect(() => {
    initializedOwner.current = read.owner
    initialized.current = false
    setForm(EMPTY_FORM)
    setDiscoveredModels([])
    setSaving(false)
    setTesting(false)
    setActivating(null)
    setDeleting(null)
  }, [read.owner])

  // eslint-disable-next-line no-restricted-syntax -- initialize each owner's form once; retries must preserve the draft
  useEffect(() => {
    if (!endpoints || initialized.current || initializedOwner.current !== read.owner) {
      return
    }

    initialized.current = true
    const current = endpoints.find(endpoint => endpoint.is_current) ?? endpoints[0]

    if (current) {
      setForm(formFromEndpoint(current))
      setDiscoveredModels(current.models)
    }
  }, [endpoints, read.owner])

  async function handleSave() {
    try {
      setSaving(true)
      const response = await saveCustomEndpoint(toPayload(form, discoveredModels))

      if (!read.isCurrent()) {
        return
      }

      setEndpoints(response.endpoints)
      const saved = response.endpoints.find(endpoint => endpoint.id === response.id)

      if (saved) {
        setForm(formFromEndpoint(saved))
        setDiscoveredModels(saved.models)
      }

      if (saved && saved.is_current) {
        onMainModelChanged?.(saved.id, saved.model)
      }

      triggerHaptic('success')
      onConfigSaved?.()
      notify({ kind: 'success', message: m.saved })
    } catch (err) {
      if (read.isCurrent()) {
        notifyError(err, m.saveFailed)
      }
    } finally {
      if (read.isCurrent()) {
        setSaving(false)
      }
    }
  }

  async function handleValidate() {
    try {
      setTesting(true)
      const response = await validateCustomEndpoint(toPayload(form))

      if (!read.isCurrent()) {
        return
      }

      setDiscoveredModels(response.models)

      if (response.ok) {
        if (!form.model && response.models[0]) {
          setForm(current => ({ ...current, model: response.models[0] }))
        }

        notify({
          kind: 'success',
          message: response.models.length
            ? m.reachableModels.replace('{count}', String(response.models.length))
            : m.reachable
        })
      } else {
        notify({
          kind: response.reachable ? 'warning' : 'error',
          message: response.message || m.validationFailed
        })
      }
    } catch (err) {
      if (read.isCurrent()) {
        notifyError(err, m.validationFailed)
      }
    } finally {
      if (read.isCurrent()) {
        setTesting(false)
      }
    }
  }

  async function handleActivate(endpoint: CustomEndpoint) {
    try {
      setActivating(endpoint.id)
      const response = await activateCustomEndpoint(endpoint.id)

      if (!read.isCurrent()) {
        return
      }

      await read.refresh()

      if (!read.isCurrent()) {
        return
      }

      onConfigSaved?.()
      onMainModelChanged?.(response.provider, response.model)
      triggerHaptic('success')
    } catch (err) {
      if (read.isCurrent()) {
        notifyError(err, m.activationFailed)
      }
    } finally {
      if (read.isCurrent()) {
        setActivating(null)
      }
    }
  }

  async function handleDelete(endpoint: CustomEndpoint) {
    if (!(await confirm({ destructive: true, title: m.deleteConfirm.replace('{name}', () => endpoint.name) }))) {
      return
    }

    if (!read.isCurrent()) {
      return
    }

    try {
      setDeleting(endpoint.id)
      const response = await deleteCustomEndpoint(endpoint.id)

      if (!read.isCurrent()) {
        return
      }

      setEndpoints(response.endpoints)

      if (form.id === endpoint.id) {
        setForm(EMPTY_FORM)
        setDiscoveredModels([])
      }

      onConfigSaved?.()
      triggerHaptic('success')
    } catch (err) {
      if (read.isCurrent()) {
        notifyError(err, m.deleteFailed)
      }
    } finally {
      if (read.isCurrent()) {
        setDeleting(null)
      }
    }
  }

  if (!endpoints && !read.failed) {
    return <SettingsSkeleton sections={[{ heading: true, rows: 3 }]} />
  }

  if (!endpoints) {
    return (
      <SettingsContent>
        <SettingsReadStatus
          failed
          hasData={false}
          loading={read.loading}
          onRefresh={read.refresh}
          title={m.loadFailed}
        />
      </SettingsContent>
    )
  }

  const allModelOptions = Array.from(new Set([...discoveredModels, form.model].filter(Boolean)))
  const canSave = form.name.trim() && form.baseUrl.trim() && form.model.trim()

  return (
    <SettingsContent>
      <SettingsReadStatus
        failed={read.failed}
        hasData
        loading={read.loading}
        onRefresh={read.refresh}
        title={m.loadFailed}
      />
      <div className="eid-custom-endpoints space-y-6">
        <section>
          <SectionHeading icon={Globe} meta={`${endpoints.length}`} title={m.heading} />
          <div className="divide-y divide-border/40 rounded-md border border-border/50">
            {endpoints.length ? (
              endpoints.map(endpoint => (
                <div className="grid gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center" key={endpoint.id}>
                  <button
                    className="min-w-0 text-left"
                    onClick={() => {
                      setForm(formFromEndpoint(endpoint))
                      setDiscoveredModels(endpoint.models)
                    }}
                    type="button"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium">{endpoint.name}</span>
                      {endpoint.is_current && (
                        <Pill tone="primary">
                          <Check className="size-3" />
                          {m.active}
                        </Pill>
                      )}
                      {endpoint.source === 'direct-config' && <Pill>config.yaml</Pill>}
                    </div>
                    <div className="mt-1 truncate font-mono text-[0.7rem] text-muted-foreground">
                      {endpoint.base_url}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>{endpoint.model}</span>
                      {endpoint.has_api_key && <span>{endpoint.api_key_preview ?? m.keySet}</span>}
                    </div>
                  </button>
                  <div className="flex items-center gap-2 sm:justify-end">
                    <Button
                      disabled={endpoint.is_current || activating === endpoint.id}
                      onClick={() => void handleActivate(endpoint)}
                      size="sm"
                      variant="outline"
                    >
                      {activating === endpoint.id ? <Loader2 className="animate-spin" /> : <Zap />}
                      {m.use}
                    </Button>
                    {endpoint.source !== 'direct-config' && (
                      <Button
                        aria-label={m.deleteEndpoint}
                        className="hover:text-destructive"
                        disabled={deleting === endpoint.id}
                        onClick={() => void handleDelete(endpoint)}
                        size="icon-sm"
                        variant="ghost"
                      >
                        {deleting === endpoint.id ? <Loader2 className="animate-spin" /> : <Trash2 />}
                      </Button>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <EmptyState description={m.emptyNote} title={m.emptyHeading} />
            )}
          </div>
        </section>

        <section>
          <SectionHeading icon={Plus} title={form.id ? m.edit : m.add} />
          <div className="grid gap-3 rounded-md border border-border/50 p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {m.name}
                <Input
                  onChange={event => setForm(current => ({ ...current, name: event.target.value }))}
                  placeholder="Axet Proxy"
                  value={form.name}
                />
              </label>
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {m.providerId}
                <Input
                  onChange={event => setForm(current => ({ ...current, id: event.target.value }))}
                  placeholder="axet-proxy"
                  value={form.id}
                />
              </label>
            </div>
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {m.url}
              <Input
                onChange={event => setForm(current => ({ ...current, baseUrl: event.target.value }))}
                placeholder="http://127.0.0.1:8081/v1"
                value={form.baseUrl}
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_12rem]">
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {m.model}
                <Input
                  list="custom-endpoint-models"
                  onChange={event => setForm(current => ({ ...current, model: event.target.value }))}
                  placeholder="gpt-5.4"
                  value={form.model}
                />
                <datalist id="custom-endpoint-models">
                  {allModelOptions.map(model => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </label>
              <label className="grid gap-1.5 text-xs text-muted-foreground">
                {m.context}
                <Input
                  inputMode="numeric"
                  onChange={event => setForm(current => ({ ...current, contextLength: event.target.value }))}
                  placeholder={m.auto}
                  value={form.contextLength}
                />
              </label>
            </div>
            <label className="grid gap-1.5 text-xs text-muted-foreground">
              {m.apiKey}
              <Input
                onChange={event => setForm(current => ({ ...current, apiKey: event.target.value }))}
                placeholder={form.id ? m.keepKey : m.optional}
                type="password"
                value={form.apiKey}
              />
            </label>
            <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={form.makeDefault}
                  onCheckedChange={checked => setForm(current => ({ ...current, makeDefault: checked === true }))}
                />
                {m.newChats}
              </label>
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={form.discoverModels}
                  onCheckedChange={checked => setForm(current => ({ ...current, discoverModels: checked === true }))}
                />
                {m.discover}
              </label>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={testing || !form.baseUrl.trim()}
                onClick={() => void handleValidate()}
                variant="outline"
              >
                {testing ? <Loader2 className="animate-spin" /> : <Zap />}
                {m.test}
              </Button>
              <Button disabled={saving || !canSave} onClick={() => void handleSave()}>
                {saving ? <Loader2 className="animate-spin" /> : <Save />}
                {m.save}
              </Button>
              <Button
                className={cn(!form.id && 'hidden')}
                onClick={() => {
                  setForm(EMPTY_FORM)
                  setDiscoveredModels([])
                }}
                type="button"
                variant="ghost"
              >
                {m.newEndpoint}
              </Button>
            </div>
          </div>
        </section>
      </div>
    </SettingsContent>
  )
}
