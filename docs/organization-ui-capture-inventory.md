# Organization UI capture inventory

This is the delivery checklist for the completed redesign, not a claim that
all screens below have been redesigned or captured. Preserve this inventory
across slices. Final delivery is a labeled gallery plus individual rendered
screenshots, saved through the supported Library workflow. State clearly that
all demonstration identities, objectives, messages and outcomes are synthetic.
Never capture the user's real profile, credentials, or installed application.

Use the supported isolated Linux Electron fixture. Record commit/tree, capture
name, route/state, theme/appearance, viewport and SHA-256 in the evidence manifest.
Capture every destination in default Eidolon, representative narrow views, and
a few alternate Settings themes. The theme regression separately exercises all
built-in palettes and appearance persistence. Existing mockup references are
input direction, not rendered evidence and not final gallery substitutes.

## Page coverage

| Surface | Required states | Current slice evidence |
| --- | --- | --- |
| Home | Overview; explicit objective intake; retained draft; empty/loading/stale | Native visual spec: wide/narrow, dark/light, intake |
| Messages | Persistent member selection; conversation; finite allowance renewal; unresolved reply | Searchable directory and recorded context: dark/light wide/narrow visual captures; native owner-chat loop reopens through Messages for retained replies, cancellation, renewal and history. Final gallery pending |
| Work Overview | Running/queued/paused work and recorded activity | Native wide/narrow dark/light overview captures; task-state and owner-request regressions |
| Objectives | Current list; search; outcome inbox; archived accepted outcome | Native visual list captures; completion scenarios retain outcome evidence |
| Objective Detail | Plan/delegation; execution evidence; review; owner intervention/resume; durable accepted outcome | Native wide/narrow progress/delegation and owner-input captures; functional loops retain acceptance/evidence |
| Organization | List/grid; persistent agent inspector; leadership/worker relationships | Native visual spec list/grid and inspector dismissal |
| Deliverables | Accepted outcomes and exact artifact/evidence inspection | Functional native loops; final gallery pending |
| Settings | All sections below, safely unconfigured where appropriate | Appearance interactions/theme persistence; final tab captures pending |
| System health | Bottom bar; details/grants/controls; disconnected, unknown, stale, disabled, degraded, attention, ready | Component state coverage + native bar/details transitions; final labeled captures pending |
| Tools and configuration | Needs You; Activity; Skills; Messaging; Execution configuration; Import history; Legacy organization | Retained routes; finish gallery and identify any legacy appearance explicitly |
| History | Expanded history and a synthetic chat | Retained mounted sidebar; final capture pending |

## Settings coverage

Settings itself must adopt the approved reference, not merely expose the theme
selector: a serif page heading, descriptive secondary navigation, roomy rounded
sections, clear label/action rows and consistent control alignment. Use the real
Settings pages and existing theme tokens. Preserve every control and its current
save behavior; a reference-wide Save button must not pretend that independently
saved settings are one transaction. Do not fabricate the reference's organization,
provider connectivity, proposed controls or future network integration. The normal
top gateway/tool bars remain removed; overall health stays in the single bottom bar.

All new interface copy is externalized through the existing catalog. English
fallback is not a completed translation. Final delivery must list remaining
translation gaps, retain reviewed existing translations, and leave user/model
content unchanged.

Enumerate the actual installed nav at capture time; do not invent unavailable
plugin or feature-flag pages. Current standard sections from the Settings owners:

- Configuration: Model, Chat, Appearance, Workspace, Safety, Browser, Memory,
  Voice, Advanced.
- Providers: Accounts, API keys (empty/masked), Custom endpoints. Local models
  only if enabled by the supported fixture flag; otherwise label unavailable.
- Gateways; Keybinds; API keys (Tools and Settings subviews); Notifications;
  Billing (synthetic/unconfigured, no purchase); Plugins; Archived chats; About.
- Any additional visible plugin settings should be listed separately. The old
  Connections alias routes to Gateways; MCP lives under Skills/Capabilities.

No final gallery should imply real voice, per-agent VMs, live model reliability,
provider connectivity, paid transactions or completion beyond the shown evidence.


## Completion audit (2026-10-11, next slice in preparation)

This is a status audit, not a finished gallery. PR64's merged native run
38106138634 verifies 73 visual captures and the functional owner-chat evidence.
PR65 owns Settings separately; its review identified a 760px inclusive/exclusive
breakpoint mismatch, and corrected 759/760/761 visibility/dimension checks and descriptive-row
text-height checks must pass before that slice is accepted.

- Organization: list/grid and wide/narrow captures exist. The next slice reframes
  the directory; retain inspector details, management and project setup forms,
  conversations/history and runtime configuration. Those secondary forms still
  need labeled final-gallery captures, including safely disabled states.
- Deliverables: the next-slice capture scenario covers session-file landing and organization
  accepted-outcome/evidence views in Eidolon Dark and AetherMesh Light, including
  narrow evidence; hosted execution and pixel inspection are pending. Exact artifact inspection still needs a gallery selection
  from the functional native delivery loop; do not substitute a fabricated file.
- Messages: next-slice native assertions explicitly traverse search, its clear
  control, filtered member selection and recorded objective link with Tab/Enter.
  Actual conversation, reply anchors, cancellation, renewal confirmation and
  retained history remain covered by the separate scripted owner-chat loop.
- Settings: every standard tab and provider/tool subview has a PR65 capture
  scenario. Remaining verification includes actual pixels, boundary navigation,
  selected appearance/reload, repeated save and interrupted confirmation. Local
  Models is unavailable unless the supported feature flag is enabled.
- Final gallery still needs systematic selections for Needs You, Activity,
  Skills/Capabilities (including MCP), Messaging, Execution configuration,
  Import history, Legacy organization, expanded History and a synthetic session.
  Preserve any visibly legacy styling and label it honestly rather than claiming
  every retained surface has already been redesigned.
- Health: current captures include disabled/ready/stale, runtime degradation and
  the advanced controls panel; disconnected/unknown/attention and related
  safely disabled controls need explicit final-gallery labels/selections.

### UI-copy audit

Existing six-locale catalogs and user/model content remain intact. New Home,
Messages, objective-overview, Settings and directory/deliverable framing use
catalog keys with explicit English fallback; this is externalization, not a
claim of six completed translations. Roster/work catalogs and organization-shell
copy retain their existing en/ja/zh/zh-Hant translations and Arabic/Russian
fallback. Owner/member and internal conversation catalogs retain their existing
six-language coverage.

Hard-coded legacy presentation still needs a dedicated migration pass in
`workspace.tsx` (objective list filters, loading, route headings and legacy
empty states), `organization.tsx` (inspector labels, assignment explanations,
unknown fallbacks), `work-graph.tsx`, `runtime-detail.tsx` (RuntimeStatus),
`agent-context.tsx` and `activity.tsx`. Backend status identifiers, IDs, capability
names, evidence bodies, user/model titles and generated content are data; do not
rewrite them as interface translations. Audit remaining labels individually and
retain reviewed translations rather than replacing them wholesale.

Final gallery assembly must use screenshots of the final merged implementation,
identify synthetic fixture data visibly in its captions, retain each original
PNG/hash/source commit/tree and include default Eidolon plus selected alternate
examples. Earlier PR artifacts document intermediate revisions and must not be
presented as one current-build gallery without recapture.
