# Reference comparison acceptance checklist

The four supplied reference images are visual acceptance targets, not only
inspiration. Each is 1536 × 961 pixels. This checklist was established after
reopening the actual local Home, Messages, Objective Detail and Settings images.
It is an unresolved work list, not a visual acceptance claim.

Compare native captures from one final merged build at **1536 × 961 CSS pixels**
(device scale recorded) beside the original images. Do not stretch a 1220/1440px
capture to imitate that viewport. Recheck representative narrow windows and the
selected alternate theme after changing structure. Archive source commit/tree,
route/state, viewport, theme and original PNG hash for every iteration.

## Current matched-image audit — PR135 candidate, 2026-10-13

Originals and native candidate PNGs are all **1536×961**, without resizing.
Candidate head `54f6202cd136a3ee1ec9f7598cdfd6e0ed6af9cb`, tree
`9a820dd5aba3e9f470db511dc83421c1726c5eaf`, was independently reviewed.
[CI 38331770120](https://github.com/AetherMesh-AI/eidolon/actions/runs/38331770120)
passed nine jobs, 1,510 selected tests and 44 native cases with zero skips/flakes.
Native evidence used merge `2bbe9b69add292b1f1810eae76d32715ba9eaa3d` with the
same tree. Artifact 11783104831 has SHA-256
`9db2e2cd8f6d20fff382a5a7bbce2755d543d2f3c5743812617d1ec4e445e905`.
All 44 provenance records, 40 translated PNGs and 11 reference PNGs were verified.
Actual Home, Messages, Objective and Settings pixels were inspected. These are
synthetic temporary profiles and scripted responses, not live-model reliability
or full reference parity.

| Page | Matched-dimension comparison | Remaining concrete gap | Bounded follow-up |
| --- | --- | --- | --- |
| Home | Both use an approximately 203px rail, unequal three-panel row and bottom activity strip. Candidate primary objective now has a distinct summary, phase and current task counts; its action is visible. | Request and accepted-delivery records have less hierarchy than the reference. The activity strip shows recorded events, without invented live presence or causal arrows. | Enrich request identity/reason and real accepted artifact metadata while retaining authoritative request and delivery actions. |
| Messages | Candidate directory/conversation boundary is around x540, versus x538 in the reference; context begins around x1218 in both. | Candidate first-message metadata starts around y308, versus y225 in the reference: recipient actions and details/formal-action rows consume reading space. Allowance and recovery controls are real requirements absent from the reference. | Put explicit chat controls beside the recipient; combine the secondary toolbar; preserve identity, opening, renewal, cancellation, drafts and history. |
| Objective | Candidate title, three manager columns and input/delivery/details aside reproduce the main arrangement. | Reference has a compact review strip near the bottom; candidate requests begin near y906 and review evidence is farther down. Runtime phase prose still uses English. | Localize known backend phase codes without translating authored text; later bring supported review evidence into the overview without inventing stage history or acceptance. |
| Settings | Candidate six-card overview retains real Model/Notifications controls and now collapses context/fallback fields without unmounting them. | First row ends around y648, versus y474 in the reference. Model stretches to notification height; lower destination cards are partly below the viewport. Reference organization-profile and global-save semantics are unsupported. | Rebalance real settings groups and spacing while retaining per-page save owners, all switches and scoped configuration. |

Coordinates above are approximate visual landmarks from the actual pixels, not
instrumented geometry. The local Messages follow-up adds native geometry evidence
for recipient/action alignment and transcript reading space; those checks have
not yet run on hosted Electron. The phase follow-up has local ledger, projection
and UI regressions but likewise awaits native verification. Neither is included
in the reviewed PR135 screenshots.

The clean organization/search/profile header, single bottom health bar,
Settings-owned themes, all six languages and narrow/zoomed keyboard reachability
remain requirements. Deeper request/settings translations, secondary-page review
and a final gallery from one merged build remain unfinished. Unsupported presence,
groups, voice, attachments and fabricated historical stages must not be added to
imitate the reference.

Estimate: the next phase/Messages milestone needs roughly 60–120 minutes including
hosted native validation once publication can proceed. The remaining complete UI
pass is approximately three to five implementation/validation cycles, about 4–8
hours plus independent review. Recent native jobs take 25–35 minutes; translation
depth and new narrow-layout findings can add cycles. This is a planning range,
not a completion promise.

## Earlier acceptance checklist and iteration history

## Shared shell

- [ ] Match reference rail proportion (about 203px of the 1536px image), brand
  scale/mark placement, navigation row height, icon weight and inset rhythm.
  The present 260px rail materially changes every content column.
- [ ] Match the reference's page left edge and hero/card gutters consistently.
- [ ] Reconcile reference Settings placement with retained Tools/configuration
  and History entry points. All existing destinations must stay reachable.
- [ ] Preserve the later explicit single bottom health bar and removal of legacy
  top gateway/tool bars. The reference's 65px organization/account/search header
  contains unsupported identity/organization affordances; report this difference
  explicitly instead of inventing an owner or bringing back legacy chrome.
- [ ] Match typography scale/weight and radii using the selected theme's colors.
  Brand/project artwork is a presentation difference; do not substitute a real
  unrelated project identity or make configured data look connected.

## Home

- [ ] Match the approximately 140px hero composition above the three-card row,
  with the reference's display-heading proportion and background artwork space.
- [ ] Match three unequal card widths, compact gutters and aligned card bottoms.
- [ ] Recompose the current-objective card's description, progress, recorded
  planning/delegation facts and supported action placement.
- [ ] Recompose owner-request cards using actual request reason, owner and real
  reply/review actions. Do not answer formal requests through ordinary chat.
- [ ] Recompose accepted-delivery preview/list using actual artifact metadata.
  Do not invent thumbnails, successful reviews or downloadable files.
- [ ] Match the lower activity strip's organizational hierarchy and spacing,
  using recorded agent/manager relationships and activity rather than a fake
  linear workflow or unsupported live-presence signal.

## Messages

- [ ] Move the heading/search into the left directory column and match the
  reference's directory/conversation/context proportions and full-height edges.
- [ ] Match recipient header/avatar placement, related-objective strip,
  transcript spacing/bubbles and composer placement.
- [ ] Match the right profile/context card hierarchy and supported evidence links.
- [ ] Preserve explicit chat opening, finite allowance/renewal, reply anchors,
  cancellation, plain-text rendering and retained history. The reference's
  unread/groups, presence, file attachments and voice controls are not all
  supported by the current member-chat authority; expose only real capabilities.
- [ ] Verify keyboard traversal and focus visibility with the actual controls,
  not only mouse screenshots. The next slice adds directory/link Tab/Enter QA;
  conversation traversal remains a separate functional verification item.

## Objective Detail

- [ ] Match hero/title/description proportions and the left-main/right-aside grid.
- [ ] Match the reference's stage-strip location and visual rhythm **only with
  recorded stage evidence**. Current phase text alone cannot prove earlier
  intake/planning/delegation stages completed or supply completion dates.
- [ ] Match progress summary and parallel manager/package cards, task rows,
  review placement and aside request/delivery/details cards.
- [ ] Preserve current-round filtering, exact package links and all existing
  intervention/resume/dispatch/priority/evidence/acceptance controls. Counts of
  finished tasks do not establish accepted outcomes.
- [ ] Report unsupported reference actions (such as sharing/exporting an absent
  artifact) instead of drawing controls that cannot execute honestly.

## Settings

- [ ] Match the reference's descriptive secondary rail and **multi-card overview**.
  PR65's single active editor panel is a functional foundation, not a match to
  that overview. Preserve every standard section and provider/tool subview.
- [ ] Match card placement, headings/icons, label/control alignment, row spacing
  and bottom action rhythm using real settings owners.
- [ ] Map profile/provider/defaults/notifications/security summaries to actual
  stored configuration. A stored key is not verified provider connectivity.
- [ ] Preserve page-specific save/autosave/confirmation semantics. The reference
  depicts one global Save transaction; current settings have independent owners.
  A cosmetic global Save would be misleading and must not be added.
- [ ] Do not invent organization profile fields, role permissions, working hours,
  network integration or workflow settings absent from the current application.
  These are concrete capability conflicts requiring an explicit design mapping.
- [ ] Verify 759/760/761px boundary behavior, non-overlapping descriptive rows,
  selected theme/language retention, repeated saves and cancelled confirmation.

## Screens without supplied images

Organization, Deliverables, lists, inspectors and retained secondary destinations
must use the same rail, hero, card, typography and spacing system. Their own
controls and provenance remain authoritative. See the capture inventory for
all secondary forms, states and untranslated presentation areas still requiring
work. The final gallery must use one current build, label all synthetic fixture
data, include the four matched screens plus all other pages, and identify any
accepted capability/health-bar exceptions. Earlier PR screenshots are iteration
evidence, not a substitute for that final gallery.


## Window fit and organization message follow-up

The later explicit goal requires main overviews and controls to fit normal
windows. Use bounded summary panels with internal scrolling/drill-down for long
lists; natural transcript scrolling is appropriate. Do not clip controls, hide
important content or shrink text to manufacture fit. Test 1536×961 reference size,
1440×900, 1280×720 and narrow widths plus zoom/reflow, with persistent navigation
and bottom health. Small windows and large text still need reachable overflow.

The current runtime has member-specific durable chats, not a shared organization
conversation. A Home entry may offer explicit selection among the current
organization's available Executives before opening that member's existing chat;
it must not silently select a globally named Executive, send, renew allowance or
create an objective. Missing connection/recipient states must be explicit.

Backend audit: persistent multiple Executives and explicit objective
executive/manager IDs already exist. `organization_identity._assign_objective`
checks an enabled Executive, enabled Manager, accepts and reporting relationship;
omitted manager selection defaults to the configured `manager` identity.
Executive hierarchical planning is `request.decompose`, while `request.plan` is
Manager-scoped. There is no `new.task` responsibility or automatic new-objective
selection by department/capacity in this path. A future typed intake router needs
explicit eligible-handler selection, one recorded accountable objective lead and
visible no-handler intervention; it is outside this UI correction and is not
claimed as implemented.

## Home fit correction under verification

Home now uses the available workspace width and unequal card proportions.
New objective opens a mounted disclosure; closing it retains the scope-keyed draft.
Record bodies scroll inside their overview cards, with dedicated full-history links.
Native fit assertions cover 1536×961, 1440×900 and 1280×720. Reflow captures
exercise 125% and 200% zoom, retaining accessible scrolling in smaller windows.
Native submission/setup flows explicitly open the intake before interacting with it.

Matched reference captures cover Home, Messages, Objective Detail and Settings.
These establish actual remaining discrepancies, not final visual acceptance.
The hero illustration, shared navigation proportions, activity presentation,
Messages header/columns and Objective Detail card hierarchy still need correction.
Settings remains a single editor rather than the supplied multi-card overview.

## Deferred organization/profile hierarchy

After the current visual rework, assess the requested `Organization name → Agent
name` hierarchy in place of default-centric profile organization. First separate
presentation grouping from storage and ownership migration. Preserve persistent
agent IDs, memory, canonical conversations, credentials, retained history and
request authority across any later move. This is follow-on planning only: no real
profile migration or ownership change is part of the current visual slices.

## Measured next corrections (2026-10-11)

Measurements below are approximate pixel landmarks from the supplied1536×961
images (allow about10px for reading image edges), compared with the inspected
ac6908b2 native captures at the same dimensions. They are targets for the next
rendered comparison, not assertions of final parity. The later requested bottom
health bar replaces the reference's old operational chrome.

| Screen | Reference landmark | Observed gap and next correction |
| --- | --- | --- |
| Shared rail | Right edge near x203 | Native rail x260 because Profile chats also sizes its shared stack. PR68 changes both defaults to203 and measures the rendered result; saved sizes are preserved. |
| Home | Three cards span about x222–1520; top near y236; lower activity near y730 | Current cards start later horizontally and dominate the height. PR68 moves the rail/gutters, gives the first objective its recorded description/action, and groups activity roles. Hero artwork and recorded request/delivery detail remain unresolved. |
| Messages | Directory x203–541, conversation x541–1218, context x1218–1536 | Current page cap/gutters put the heading above all three columns and leave large blank space. Next slice moves the heading/search into the directory, uses approximately25%/51%/24% columns and full-height separators, then checks actual retained-chat content separately. |
| Objective Detail | Main/aside divide near x1120; parallel manager cards share the main column | Current progress and single manager card stack vertically, leaving tasks below the fold. Next slice uses a compact progress summary followed by parallel recorded package cards. Never infer completed stages from phase text. |
| Settings | Secondary rail x222–458; overview spans x465–1519 in two card columns | Current selected editor is one large panel. Add a real read-only overview linked to existing save owners; do not fabricate reference profile fields, provider connectivity or a global Save transaction. |

Capture gates: wait for the actual Settings Apply control before its reference
image; prove zoom CSS viewport and device scale before reflow images; check focus
outline geometry against clipping ancestors and inspect the complete outline in
pixels. ac6908b2's first Settings reference capture was a loading skeleton, and
its zoom metadata did not establish intended reflow. These images cannot close
the corresponding acceptance items despite a green run.

### Settings overview cards (draft)

The default Settings destination now composes existing Model and device
Notifications editors in separate cards, with explicit entries to Providers,
Safety, Workspace and Appearance. Each editor retains its own scope, save,
confirmation and persistence behavior. Only one ConfigSettings owner mounts at
a time; there is no aggregate Save transaction or invented organization profile,
working hours, peer connection, role permission or executive messaging control.
The supplied reference still differs in artwork and unavailable feature content.
Native reference/narrow captures and independent-save checks are pending.
## Messages composer iteration

Retained-chat capture at the reference size exposed a separate gap after column
geometry was corrected: explanatory text and allowance details pushed composing
below the initial view. The isolated follow-up bounds the conversation column
and gives its transcript keyboard scrolling. Conversation policy and reply-anchor
details remain in accessible disclosures; provider usage notice, remaining calls,
reserved-token ceiling, explicit renewal, send/cancel and recovery stay available.
Native checks cover reference/short/narrow sizes, real zoom, long history, a
pending reply and a retained read-error state. This iteration is not accepted
until current-head native checks and actual pixels pass. Final reference parity
and the complete gallery remain outstanding.

### PR71 native follow-up

The first exact-head native run failed transcript focus clipping at 760px; it did
not reach the new zoom, cancellation, long-history or read-error checks. The
follow-up centers keyboard-focused workspace transcripts inside their ancestors,
provides wider focus clearance and preserves a three-pixel outline at fractional
zoom. Workspace allowance and provider-charge text now accompany the composer;
inline chat retains its prior ordering. Native checks measure the actual inner
panel overflow and require charge text, allowance and explicit action in view
with the focused composer. Corrected native evidence is still required before
acceptance; the earlier outer-panel assertion alone was insufficient.
## Objective composition iteration

The next isolated Objective slice removes the centered 1120px page cap, uses
reference-sized main/aside proportions, and puts recorded phase and task counts
beside the progress heading. Manager assignment cards retain their existing
current-round data and explicit work/evidence controls. No completed stage dates,
acceptance, delivery or operational capability are inferred from progress.

Native checks measure the left edge/main width within 10px of the approximate
reference landmarks, three recorded assignment cards on one row at 1536×961,
and first-assignment work above the fold at 1280×720. They also exercise the
three overview review controls with visible keyboard focus at 100%, 125% and
200% native zoom. Natural page overflow retains all detailed controls. These
checks and matched captures must pass before treating this iteration as verified;
the reference stage strip, artwork, review composition and final gallery remain
outstanding.

### Objective controls beside the reported phase

Pause/resume and priority now occupy the main-column phase panel before progress
and delegated work, retaining their existing scoped component owners. Current
state, running stages, explicit mutations, uncertain retries and errors remain
visible. Longer explanations use named disclosures; closing them does not reset
drafts or retry intent. The runtime reports one current phase, not an ordered or
dated stage history, so this panel makes no historical stage-completion claims.

The pre-change native baseline at 1536×961 placed the main column at x222/y254,
with manager cards at y520–766. The supplied reference has a stage/control panel
near y215–380 and cards near y470–835. Card height also depends on task count:
the matched baseline has one task per card, while a separate new dense capture
exercises three. These are separate comparisons, not evidence for forcing a
fixed card height.

Native verification retains the short-window first-task/assignee requirement,
measures the actual phase/control/progress order, and tests button, select and
disclosure focus at 100%, 125% and 200% zoom. Real temporary-ledger scenarios
activate pause and priority save with Enter, retaining their existing reload,
denied-resume, unchanged-budget and exact uncertain-retry assertions. Updated
geometry and native receipts are required before accepting this iteration.

### Objective fractional-zoom focus follow-up

The short-height correction passed its title/assignee checks, but native 125%
zoom rounds a two-pixel outline to 1.6 CSS pixels. Objective controls now use a
three-pixel outline; all existing minimum width, contrast and clipping checks
remain unchanged. Corrected exact-head native evidence is pending.


## Current reference audit after PR71 (2026-10-11)

The earlier coordinate table above describes the pre-PR68 comparison. It is
historical, not the current layout. Reopened the original Home reference and
inspected `home-fit-1536-961.png` from native run38124590239, tree
`5ce1131b4c399eda3f2dfbcac10b9db4f354a680`.

- Home cards now start near y202 (reference y206), end near y632 (reference
  y695), and activity starts near y644 (reference y705). Both activity panels
  are approximately240px tall. Reducing activity height would not match the
  reference. The current retained-record notice, bottom status bar and outer
  spacing consume vertical space absent from the original composition; preserve
  their information when adjusting hierarchy. No CSS change was made from an
  incorrect assumption that the reference activity panel is180px tall.
- The latest Settings Overview places cards near x502/y238, versus roughly
  x469/y180 in the reference. Its additional Overview heading and secondary-rail
  spacing are the concrete next comparison targets. First resolve the config
  loading failure; loading skeleton captures cannot validate this composition.
- Objective metadata currently supplies one optional phase string, current work
  packages and acceptance evidence, not a complete ordered phase history.
  A completed multi-stage strip and stage timestamps cannot be inferred from
  that current phase. Keep the unknown-state behavior in objective-progress
  tests while choosing any additional recorded-evidence representation.
- OrganizationSnapshot has no organization display name or owner account
  identity. The current command palette is not unified agent/objective search.
  Reference header fields must have real owners and actions before presentation.
- Scenic artwork remains an original-asset task. No screenshot fragments or
  unrelated Lunavale assets were copied into production. Branding and final
  reference parity remain open.

Current chat verification covers11 captures and22 focus checks, including long
history and a short English retained-read error. It does not cover every long
translation, expanded disclosure or uncertain-send combination. At200% zoom,
keyboard focus brings each target into view; the entire transcript and composer
are not claimed to be simultaneously visible.

The first Objective-controls native run (38137080891, head c0293401, tree 5071de4e) retained a 1536×961 dark capture with phase y228.33/h146.52, progress y390.84/h99.28 and cards y532.72/h246.64. The main column remained x222/w888.63. Twenty native scenarios passed, including real pause/resume; priority focus measured a 1px animated outline and the dense fixture did not refresh on a synthetic window-focus event. No short-window or full zoom acceptance is claimed from that failed run. The follow-up excludes outline width from Objective-button transitions, reloads the dense fixture through the existing snapshot path, and trims 12px of phase spacing. Existing focus, viewport and durable-state assertions remain unchanged; fresh native evidence is required.

Run 38138081494 on a760e939/tree 98c3eedc passed 21 native scenarios, including both real keyboard mutations with 3px outlines, and delivered the dense fixture correctly. The inspected wide cards moved up exactly 12px to y520.72 (phase h134.52); the dense capture uses normal vertical workspace scrolling. The unchanged short-window first-assignee assertion then found Robin only 28.8% visible above the health bar at 1280×720. A short-window-only follow-up reduces the owner-row vertical margins from 18px to 10px, preserving text and control sizes. Additional native coverage checks all three dense cards, opened English explanations at each zoom, and a real Japanese locale at wide, short, narrow and 200% sizes. These changes require delta review and fresh passing evidence; no successful full native pass is claimed yet.

Run 38139063787 on reviewed 38d983af/tree 35639150 retained 165 organization captures with verified hashes and no horizontal overflow, including both short-window assignees, all dense-card assertions, and the opened English zoom states. Twenty-two scenarios passed. The aggregate main visual scenario hit its unchanged six-minute total budget late in theme persistence; that run remains failed. The added Japanese scenario passed geometry/focus assertions, but actual PNG inspection found missing CJK glyphs in the hosted Linux image, so translated readability was not accepted. The bounded harness follow-up installs fonts-noto-cjk in CI and logs Japanese font selection, and gives only the enlarged main visual scenario an eight-minute aggregate budget. Every individual assertion deadline, no-retry policy, native job budget and product source remains unchanged. Fresh readable Japanese pixels and a complete successful native run are still required.

## Tall Home activity spacing

Accepted main 1ea9bccc at 1536×961 places cards at y202.23–673 and activity at y685–925, with the health bar at y933. The private reference has card bottoms around y695 and activity beginning around y705. This bounded iteration reallocates 22px from the tall-window activity panel to cards: activity maximum height 240→218px, vertical panel padding 16→12px, heading gap 10→6px, event padding 12→9px and event paragraph margins 10→8px. Font sizes, records, list bounds, links, controls and the health bar are unchanged. These rules apply only at widths above 1100px and heights at least 900px; the 1280×720 layout retains its existing rules.

Native verification must show the actual resulting geometry and every one of the four recorded event cards and timestamps visible at 1536×961 and 1440×900. The matched 1536 card-bottom check permits 5px around the approximate reference. Existing short-window contained-scroll, focus, intake retention and health-bar assertions remain. No passing result or exact final geometry is claimed before inspection. The reference's lower activity boundary extends beyond the app's persistent health bar, so this change does not claim complete vertical parity or add a blank Messages header.

PR79 verification on 9763a26a/tree d92bbf52 completed in run 38139635971: all nine checks, 887 selected tests/types and 22 native scenarios passed. Actual 1536×961, 1440×900 and 1280×720 pixels were inspected; all four event cards and timestamps fit the two tall windows. Measured card bottoms are 695, 634 and 517 respectively, activity top/height are 707/218, 646/218 and 529/155, and health-bar tops remain 933, 872 and 692. Short-window contained scrolling remains necessary. The guarded merge produced main 629cebf8 with the same reviewed tree; postmerge run 38140970224 is tracked separately. This proves the measured fixtures, not arbitrary translated record lengths.


## Shared workspace header and loaded-record search

PR78's independently reviewed combined head 42060fbc/tree ee25f775 passed all nine checks in run 38141052649: 888 selected tests, all three TypeScript projects, and 23 native scenarios in 11.1 minutes. All 168 organization PNG hashes, contrast and overflow receipts passed; 42 action-focus checks and 28 Japanese focus checks passed. Actual Japanese glyphs are readable after hosted Noto CJK installation, including native 200% captures. Wide/dense/short and opened English captures were inspected. Guarded merge main 12ec6563 retains the exact tested tree; postmerge run 38142243548 is separate and pending. PR79 postmerge run 38140970224 passed all nine checks, 887 selected tests and 22 native scenarios; retained Home pixels and geometry were rechecked.

The next candidate adds one stable workspace header with the actual profile manager, connection switcher, scoped attention picker and contextual Settings search. It removes the duplicate Settings search pill and moves attention out of the bottom health bar; existing advanced controls stay in health details. Header search inspects only loaded objective metadata, member identities and internal conversation subjects/participants. It neither fetches full history nor opens, renews, sends or acknowledges conversations. Current transport scope is checked again at activation, and stale scope closes the search. Existing page/draft owners are not keyed or replaced by search. Escape closes only this dialog, preserving an underlying agent inspector.

Top spacing is rebalanced for Home, Objective and Settings while retaining the accepted short-window visibility assertions. New native coverage targets both themes, 1536×961, 1280×720, 760×900 and 200% zoom, read-only conversation inspection and exact unsent owner-chat DOM retention. This candidate has no native acceptance yet. Original scenic artwork, more complete review composition, and final reference/gallery acceptance remain open; no exact scenic parity is claimed.

Independent review of PR80 initial head 00098960 found two blockers before native acceptance: its new E2E used unsupported Settings tab `appearance` instead of `config:appearance`, and controlled search lacked a Radix trigger or close-autofocus handler. A focused regression reproduced focus landing on body after Escape (one failed, three passed). The follow-up uses the supported route and a stable header-button ref with an explicit close-autofocus handler, gated by the current transport scope and enabled/connected trigger. Escape still leaves the underlying inspector open. The native focus-return assertion remains unchanged. New workspace/header copy currently uses English fallback; translation work is not complete. Fresh exact-head native evidence remains required.

The PR80 review follow-up also restores Settings search's existing localized `settings.search.pill`, supplies all new workspace copy for Japanese, Simplified/Traditional Chinese, Arabic and Russian, and adds native Japanese/Arabic/Russian header coverage in both themes at short, narrow and 200% sizes. Arabic checks document RTL direction. A second temporary loopback connection exercises the actual registry/switcher menu and long-label geometry; a pending-connection component regression ensures stale search closes and does not reopen. The header permits the switcher's external focus outline without changing inner-label truncation. These changes cover the new vocabulary, not all previously untranslated UI. Native results and pixel review are pending.

PR80 initial run 38142901115 was cancelled on supersession, not accepted. Its retained real owner-chat trace confirms the new search draft/DOM-retention checks passed and records a later short read-error composer failure: inner panel overflow was 24px at 1280×720, against the unchanged maximum of 1px. Actual failed pixels were inspected. The follow-up reclaims the header's 30px from conversation-only bottom and recipient spacing at short desktop sizes; fonts, controls and directory heading position stay unchanged. The same no-scroll/viewport checks remain, and matched Messages checks now explicitly require content origin within 1px of y64. Fresh native proof is required. PR78 postmerge run 38142243548 passed all nine checks, 888 selected tests, all types and 23 native scenarios in17.4 minutes; artifact audits and Japanese/short-window pixel reinspection passed on main12ec6563/treeee25f775.


## Independent local continuation after consolidation

The Objective Detail reference was reopened beside the PR131 native capture at
1536 × 961. The next isolated slice reuses theme-derived decorative scenery in
the objective header, places status beside the title, and adds a compact aside
with recorded creation time, executive ownership, priority and phase. It does
not manufacture stage history, delivery dates, project identity or acceptance.
The same slice moves objective-list navigation and runtime connection warnings
into all six language catalogs. Authored content and canonical IDs are preserved.

Native coverage is prepared for objective search and the details card across
all five non-English locales, dark/light, normal desktop and 200% zoom. Existing
read-only RPC and zero-provider assertions remain. This is preparation, not
visual acceptance; the full reference discrepancy checklist remains open.
These UI changes are excluded from consolidated PR133.

UI baseline 82e292c0fb931b1f79b1e251c51c41ab450eba84 (tree
ff08810dec1cb1580038709e7d9aaff2d57036e7), run 38323021547, passed
1,495 selected component/helper tests and 44 native cases (41 standard plus
three directory cases; no native skips/flaky/unexpected results). Artifact
11780915305 SHA256 e2823163da458a644959f644955e7b8444c7e0658886826a465928cef719cd08
was verified against the exact source: 34 inline provenance records and ten
file provenance records. Representative English objective, Japanese and Chinese
lists, Traditional Chinese objective, Arabic RTL objective and Russian 200%
objective captures were actually inspected. The new details and list copy are
visible; large text uses reachable scrolling, not simultaneous whole-page fit.
This is not final reference parity or a completed all-screen gallery.

Those pixels exposed inherited English overview labels. The next UI-only slice
provides full objective-overview copy for all five non-English catalogs, retaining
existing localized plural functions, authored phase/task text, review truth and
controls. Objective inspector and other legacy labels still require follow-up.
It also adds the visual suite's font/two-animation-frame barrier before native
directory captures: PR133's final artifact had passing DOM assertions but several
PNGs still showed earlier frames. Original artifacts are preserved. This capture
change must be validated with fresh pixels; it does not change directory behavior,
assertion deadlines, retries or production activation.


## Runtime phase localization follow-up (local; native verification pending)

The gateway historically emits `phase` as English prose, while older runtimes
and prototype metadata can contain free text. The additive `phaseCode` projection
uses stable values for executive/legacy planning, execution/review, intervention,
integrated acceptance, accepted delivery and legacy reviewed outcomes. The
existing `phase` text and transition precedence remain unchanged.

The renderer localizes only recognized string codes from runtime objectives.
Missing, malformed and future codes fall back to the exact recorded text;
prototype phases remain authored even if a code is present. This does not infer
stage history or accepted delivery from task completion. Real-ledger tests cover
intervention/resume and durable acceptance, including 100% completed tasks before
final acceptance, plus retained legacy shapes. Native translated fixtures now
exercise typed phases while keeping English authored titles/descriptions in
Arabic interfaces; those captures require a later hosted run.
