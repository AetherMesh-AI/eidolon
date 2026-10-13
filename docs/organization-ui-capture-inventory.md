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

## Final acceptance checklist — current candidate

The last fully verified candidate is PR137 head
`e6e74ff582fbb56a66ab807a851c61adcc776b2e`, tree
`f7f244c18456fda2e4d527a6a0046a8c474f6ae9`, in
[run 38472282716](https://github.com/AetherMesh-AI/eidolon/actions/runs/38472282716).
All nine checks, 1,581 selected component tests and 53 native cases passed with
zero skips, retries or flakes. Independent source review cleared that exact tree.
Independent pixel review accepted Settings desktop composition (vertical
notifications, 275px middle row and compact bottom band). Home, Messages and
Objective structural acceptance from `a9a43464` stands. Do not reopen those
layouts as part of remaining-page work.

Ten Settings images from `e6e74ff5` were delivered to Library with successful
metadata writeback. They include light/dark desktop, narrow, light 200%, and
JA/AR/RU notification focus at both zoom levels. The accepted composition does
not excuse the independently confirmed Arabic checked-thumb overflow. The next
candidate corrects direction-aware movement and adds native containment/state
checks. The first expanded gallery at `332a1013` failed: all six locales exposed
the Appearance pet picker's clipped final focus outline; Arabic Tools exposed a
clipped list outline. English/Arabic checked, unchecked and disabled switch
captures reached at 100% show contained thumbs, but 200% was not reached and no
complete gallery pass is claimed. A separate Profiles probe entered CodeMirror,
where Shift+Tab intentionally indents; the probe now approaches from the previous
control without altering any outline assertion. Repairs add scroll padding to
the demonstrated clipping containers. Independent destinations continue collecting
evidence after a failed assertion, and the case still fails if any assertion failed.

The `fccba238` expansion retained additional dark-mode evidence but still failed.
Traces confirmed clipped Profiles list controls and the same short-height failure
across Settings editors, plus normal-height About/provider-key edge clipping.
The next repair extends the existing page scroller to all Settings sections below
600px and adds focus clearance to the demonstrated list/editor boundaries.
Pixel inspection also caught an Activity avatar stretch introduced by the new
text-column selector; that selector is narrowed and native square-avatar geometry
is asserted. Fixture corrections use stable expanding-credential locators, seed
durable history before initial backend reads, and select AetherMesh for actual
light surfaces because Eidolon's two appearance preferences intentionally remain
dark. No complete gallery pass is claimed for either failed candidate.

“Captured” means retained native evidence exists, not final pixel approval.
Only Home, Messages, Objective Detail and Settings have supplied image references.
Other destinations require consistent shared design, not an invented reference.

| Surface | Retained evidence / accepted scope | Still outstanding |
| --- | --- | --- |
| Home | Structural reference pixels accepted; intake and durable-state regressions retained | Final merged-build gallery and exceptional-state inventory |
| Messages | Structural reference pixels accepted; directory, conversation, renewal and retained drafts verified | Final gallery and deeper action-copy audit |
| Objective Detail | Structural reference pixels accepted; review evidence, intervention and accepted outcomes verified | Final before/after intervention and outcome gallery |
| Work Overview | 26 captures across translated normal/200% and wide/narrow appearances | Shared-design pixel acceptance and final gallery |
| Objectives | 44 list/outcome captures; search and routing verified | Empty/loading/stale and archived-outcome gallery |
| Organization | List/grid/inspectors and selected staffing states captured | Secondary forms and locale audit |
| Deliverables | 26 captures; recorded artifact/evidence inspection and routing verified | Final exact-artifact, empty/loading/error gallery |
| Settings | Desktop composition accepted; 53 captures cover standard destinations, subviews, breakpoints and selected themes; 48 notification focus checks pass | RTL thumb fix; all-section locale/zoom/focus review; final gallery |
| System health | Single footer and owned details; ten captures plus state regressions | Complete state/theme/zoom gallery; never infer provider health |
| Secondary pages | Shared headings and labeled Activity filters prepared; 42 affected component regressions pass | Dedicated hosted six-locale, both-mode, normal/200% gallery not yet verified |
| History | Existing persistent history behavior preserved | Expanded history and actual synthetic-chat gallery not yet verified |

The next gallery scenarios cover Needs You, Activity, Capabilities (Skills,
Tools and MCP), Messaging, Execution Profiles, Import history, Legacy history,
expanded chat history, and 21 Settings destinations/subviews. Separate bounded
Linux jobs retain the existing native suite's deadlines, assertions and retries.
Synthetic history setup uses one loopback scripted provider call; page browsing
must make no additional provider calls. Settings uses zero provider calls.

Final release gates remain:

- [x] Four reference compositions independently accepted within their stated scope.
- [ ] RTL checked/unchecked/focused/disabled thumb containment verified natively.
- [ ] Every available requested destination and relevant empty/loading/error state included.
- [ ] Default dark/light and representative alternate themes inspected.
- [ ] EN/JA/ZH/ZH-Hant/AR/RU interface copy audited; English fallback explicitly incomplete.
- [ ] Narrow/short and 200% keyboard paths verified; scrolling distinguished from simultaneous fit.
- [ ] Existing navigation/draft/identity/intervention/acceptance regressions pass on final source.
- [ ] Labeled gallery and original screenshots from one final merged build delivered to Library.

Merge remains on hold pending authorization. Candidate evidence can be reviewed
and delivered now; a merged-build gallery cannot be claimed before that gate.
Real voice, per-agent VMs and live model reliability remain outside this UI scope.

## Authorized follow-on: budget-aware Fixed/Auto routing

Explicitly confirmed by the user and queued after the UI acceptance milestone;
do not interrupt the current fixes or
gallery delivery. No implementation is included in this UI candidate.

- Extend durable objective admission accounting in `organization_budget.py` and
  policy parsing in `organization_policy.py` to project, manager and agent scopes,
  with atomic concurrent reservations and capacity retained for review.
- Make provider/model allowlists and configured fallbacks explicit. The root
  executive stays Fixed; assignment-level Auto proposals may select model and
  reasoning only within runtime-enforced limits and existing data destinations.
- Bound retries, context and concurrency at admission/execution. Account for
  review, rework, integration and tool costs; unknown or interrupted usage must
  not become a fictitious refund or a measured zero.
- Show cost/quota evidence and distinguish configured ceilings, measured usage
  and unknown subscription allowance. Do not invent current subscription quota.
- Give managers remaining allowance, in-flight reservations, reset/window time,
  user priorities and completion/review reserve. Proactively reduce concurrency,
  defer low-priority work and choose cheaper capable allowed models as headroom
  shrinks, while hard admission guards remain the backstop.
- Accept manually entered subscription percentages with timestamp, uncertainty
  and a conservative buffer. An opaque percentage is not an exact token balance;
  slowing concurrency alone does not reduce total consumption. Where provider
  metering is unknown, distinguish local enforcement from an externally guaranteed
  cap. Preserve user priorities, permissions and configured data destinations.
- Preserve durable agent identity and memory across model changes. Start with
  deterministic routing rules and evidence-based escalation; learned routing
  follows measured accepted-task outcomes, not optimistic task completion alone.

The existing reservation seam, executor (`organization_executor.py`), owner-chat
executor and tool audit accounting are starting points, not proof this feature
already exists. After UI acceptance, report a repository-grounded sequence and
regression plan before expanding these contracts. The feature authorizes no new
paid calls, subscriptions, credentials, provider destinations or security changes.

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
