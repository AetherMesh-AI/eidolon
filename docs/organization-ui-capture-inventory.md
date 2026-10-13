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

## Final acceptance checklist — 2026-10-13

Overall reference parity is **not signed off**. The verified baseline is PR137
head `7dfbefb2566ba2915a35696c52f209ad70f719ac`, tree
`62d9453bcf7a399eea39dafea95fcdef2ce3706c`, in
[run 38348587837](https://github.com/AetherMesh-AI/eidolon/actions/runs/38348587837).
Its nine checks, 1,541 selected tests and 44 native cases passed with zero
skips/flakes/retries. Independent bounded source/CI review cleared that exact
head. Pixel inspection below was performed by the implementing agent; it is not
an independent final visual signoff. Merge remains on hold.

Accepted/proven within that baseline; do not reopen unrelated backend audits:

- [x] Persistent identity/context, requester attribution, finite chat renewal and retained drafts preserve their existing contracts.
- [x] Current-round review findings stay read-only; unmet counts/conflicts remain visible and final acceptance remains independent.
- [x] All eight notification switches, explanation access, keyboard controls and independent Settings save owners are retained.
- [x] Full review-panel border clears the footer (6.33px at 1536×961); Settings first row is 295.64px, near the approximately 295px reference.
- [x] Existing navigation/recovery and theme-persistence checks passed; sampled normal/narrow/200% evidence is retained.
- [x] Four interim Home/Messages/Objective/Settings images from PR135 source `54f6202c` were delivered through Library; the local delivery receipt records success and the parent confirmed receipt. This was interim delivery, not the final all-page gallery.

The next Messages sizing delta and new Work Overview normal/200% coverage require
fresh native evidence and renewed source review. They do not change the accepted
snapshot above. Existing tests remain regression guards for later presentation
changes; their presence does not imply those features need redesigning again.

“Captured” below means retained native evidence exists, not that every image has
received final pixel review. Only Home, Messages, Objective Detail and Settings
have supplied image references; the other pages need shared-design acceptance,
not a fabricated claim of matching an absent reference.

| Page | Reference / shared design | Themes and viewport evidence | Localization and navigation | Remaining acceptance |
| --- | --- | --- | --- | --- |
| Home | Reference composition partly matched; question/requester hierarchy implemented | Dark/light, 1536×961, short/narrow, 125%/200% captured | Six-catalog core labels; intake retention and explicit-recipient navigation tested | Final matched pixels, deeper request text and accepted-outcome hierarchy |
| Messages | Three-column reference arrangement and extra transcript space implemented | Dark/light wide/short/narrow; translated 200% captures; AR/RU retained context captured | Persistent recipient/history, finite renewal and cancellation tested; core six-catalog labels | Full-width search correction pending native; header/member-row polish, final populated/empty/recovery gallery and deeper action-copy audit |
| Work Overview | Shared design, no supplied reference | Dark/light wide/narrow captured | State/owner-request routing tested; core copy covered | English 200% and five-language normal/200% checks prepared, not yet verified; final pixels/gallery remain open |
| Objectives | Shared design, no supplied reference | Dark/light wide/narrow and translated normal/200% captured | Search and outcome routing tested; six-catalog list labels | Final empty/loading/stale and archived-outcome gallery |
| Objective Detail | Reference composition partly matched; current review strip y825.25, disclosed evidence authoritative | Dark/light, short/narrow, 125%/200%; 20 translated review images verified | Pause/resume, priority, owner-input and accepted-delivery paths tested; six-catalog phases/review labels | Border correction accepted; final matched-reference visual signoff and before/after intervention/acceptance gallery remain open |
| Organization | Shared design, no supplied reference | Dark/light list/grid/inspector, narrow and translated 200% captured | Persistent identities, role/context labels, inspector and directory navigation tested | Secondary staffing/project/configuration forms need final gallery and locale audit |
| Deliverables | Shared design, no supplied reference | Dark/light session files and accepted evidence, narrow and translated 200% captured | Actual recorded artifact/evidence inspection and source routing tested | Final gallery of exact accepted artifact, empty/loading/error cases |
| Settings | First-row height correction accepted: 295.64px vs approximately 295px | Overview dark/light; narrow/200%; standard sections captured; three themes with focus/narrow checks | All eight switches, disclosures, Model drafts/Apply and independent saves tested | Height correction accepted; remaining card/header treatment, all section pixels/locales and final gallery remain open |
| System health | Single bottom bar and owned details | Multiple real status fixtures and narrow details captured | Core six-catalog health labels and state/navigation checks | Final all-state light/dark/200% gallery; no inferred provider health |
| Tools/configuration | Existing destinations retained | Coverage varies by destination; no comprehensive final gallery | Needs You, Activity, Skills, Messaging, Execution, Import history and Legacy routes need final inventory | Shared-design, both themes, locales, 200% and navigation signoff per available destination |
| History | Existing mounted history retained | No final expanded-history/synthetic-chat gallery | Persistent-history behavior covered separately | Shared-design, both themes, locales and 200% visual signoff |

Genuinely outstanding final release gates (bounded functional acceptance above is retained):

- [ ] Four matched-size reference comparisons approved; residual mismatches named.
- [ ] Every available requested destination and relevant empty/loading/error state included.
- [ ] Default dark/light plus representative alternate themes inspected; theme persistence retained.
- [ ] EN/JA/ZH/ZH-Hant/AR/RU interface copy audited; English fallback explicitly marked incomplete, authored records unchanged.
- [ ] Narrow/short and 200% keyboard paths verified; reachable scrolling distinguished from simultaneous fit; focus outlines and footer clear.
- [ ] Reconfirm the already accepted navigation/draft/identity/intervention/acceptance paths on the final build; change them only for demonstrated regressions.
- [ ] Labeled gallery and original screenshots from one final merged build delivered through Library with source/tree, dimensions and hashes.

Remaining estimate: Messages sizing plus Work Overview coverage needs one hosted
validation/review cycle, typically 45–75 minutes. Broader presentation, deep
locale review and final-gallery acceptance remain approximately two to four
cycles / 3–6 hours plus review/authorization delays, with low confidence. Final
merged-build gallery delivery cannot complete while the merge hold remains;
interim screenshots have already been delivered and native candidate artifacts
remain available for review.

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
| Settings | All sections below, safely unconfigured where appropriate | Standard tab/subview capture scenario passed at the verified baseline; final all-section pixel review and gallery pending |
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

- Configuration: Model, Chat, Appearance, Workspace, Safety, Browser, Memory & Context,
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
