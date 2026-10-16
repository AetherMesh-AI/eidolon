# Workspace cohesion audit

Baseline: `d825aebce477d79977a1d1c6a62c8a078c5ce502`. Source audit of the current desktop, with synthetic exact-source native journey coverage added alongside the repairs. A source inspection or a passing component test is not a visual/native acceptance result.

## Navigation model

- Home: what is happening, what needs a decision, and the next useful action.
- Messages: a selected persistent member and their existing conversation. Selection is navigation only; Chat remains an explicit authority-bearing action.
- Work: objective overview, with Objectives and Activity as named contextual destinations. Objective detail belongs to this section even when entered from another surface.
- Needs You: a first-class decision queue, with its count beside that destination.
- Organization: people, roles and organization management.
- Deliverables: defaults to organization evidence/accepted outcomes; session attachments remain an explicitly separate source.
- Settings: configuration, including contextual links to Skills, messaging integrations and execution profiles.
- History: a named disclosure for the retained session tree, profile chats, imports and legacy views. The tree stays mounted.

All existing destination URLs remain valid. Primary section selection and page selection are distinct. Return links are restricted to known local routes and bound to the same owner scope. A route must never open a provider conversation, send a message, resolve a request or download/apply a deliverable implicitly.

## Findings and bounded repairs

### P1: unrelated destinations were grouped under an unlabeled ellipsis

`src/app/eidolon/rail.tsx` mixed urgent owner decisions with configuration, history and optional profile chats. Work and Objectives were peer primary destinations although they expose the same work. The rail now gives decisions a visible home and places secondary links in their named context. History remains accessible without destroying its state. Existing URLs are retained.

### P1: clicks lost their context

`messages.tsx` kept recipient selection only in component state. Leaving for linked work and returning lost the selected member. `runtime-detail.tsx` and the unavailable branch of `runtime-history.tsx` always returned to Objectives, regardless of origin. `home.tsx` said Review request but linked only to the objective top.

The repair uses URL-backed owner/identity-bound recipient selection, safe origin-aware objective return, and exact request focus. A request intent is consumed once per navigation, after the matching owner is ready; polling cannot reopen closed or invalidated inspectors. Existing ancestor trails, drafts and asynchronous response fences are retained.

### P1: Deliverables landed on the wrong source

`artifacts/workspace.tsx` already provides the correct separation between session files and organization evidence. The rail formerly linked to `/artifacts`, whose default is session files; Home already used `?source=organization`. The repair aligns the rail with Home and preserves old session links. No ledger record is promoted to an accepted output, copied into session history, or opened automatically.

## Remaining follow-on issues

These are identified concerns, not claims of completed visual verification.

- **P2, Organization/AgentProfile:** `organization.tsx` includes roster metadata, milestones, goals, routing, management, projects, member list, conversations and management history on one page. A member opens an inline Inspector, while Messages presents a different profile/context column. Follow-on: establish a single member-detail presentation and grouped People / Direction / Configuration sections without moving write authority.
- **P2, Work/ObjectiveDetail:** `work-overview.tsx` has a plain no-work sentence and then Activity; objective detail has multiple controls before its tabbed content. Follow-on: give empty Work a direct next action and review objective information hierarchy against real long content. Preserve task/review/acceptance semantics.
- **P2, loading/error honesty:** `artifacts/index.tsx` turns a top-level indexing error into an empty array after a transient notification. It also gives a filtered no-match result the same no-artifacts copy. Follow-on: persistent recoverable error/stale state and distinct filter-empty copy. RuntimeArtifact already has explicit loading, retry and refresh, so reuse that standard.
- **P2, back/filter restoration:** objective list filters and pagination are local state in `workspace.tsx`; restoring the destination alone does not restore every filter. Add URL-backed list controls after the first journey is verified.
- **P2, accessibility/localization:** `runtime-detail.tsx` tab roles lack a complete roving-tab keyboard contract; `inspector.tsx` constructs English labels and kinds; `organization.tsx` has English assignment/status fallbacks. Add keyboard-pattern and localized-label coverage rather than treating first/last focus checks as a full accessibility audit.
- **P2, layout/scrolling:** `eidolon.css` layers several Messages responsive overrides and nested scrollers. Inspect transcript/composer visibility, long titles, member directory, open details and nested inspectors at 100%/200%, light/dark, and narrow windows before simplifying styles. A valid screenshot size alone is not proof of usability.
- **Settings:** the existing overview has distinct section owners and one page scroller; contextual rail links now expose related destinations. Its many controls require full keyboard and error-state review, not only screenshots of the initial page.

## Verification contract

Component coverage must establish safe-origin rejection; owner/identity isolation; selected-recipient Back/Forward restoration; exact hidden-by-filter request focus; loading→ready focus; Close/Escape and removed/readded non-reopening; ancestor draft retention; delayed-response isolation; and preserved legacy/source links.

`e2e/organization-navigation-journey.spec.ts` uses the existing isolated native gallery fixture and synthetic records. It exercises real clicks through Home → Messages → objective → Messages → Work/Back/Forward → Home request → Home → Deliverables → Settings → Organization, captures page and zoom evidence, and asserts no unexpected organization writes or provider calls. A second native scenario covers empty primary pages and an injected read-only observation failure with explicit Retry recovery. Existing gallery suites cover secondary/settings locales and themes. Native proof and screenshot review must be completed against the final union commit before merge.
