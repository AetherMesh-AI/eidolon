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
