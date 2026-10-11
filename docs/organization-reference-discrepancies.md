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
