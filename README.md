# Eidolon

Eidolon is AetherMesh's open-source desktop agent manager, under active development. Its direction is simple: give each agent an ongoing identity, a dedicated conversation, and useful context that can carry forward as you work together.

The project brings agent interaction, local data, and coordinated work into one application. You should be able to return to an agent, continue your conversation, and understand what it is doing without managing a collection of disconnected sessions.

The planned `AetherMesh-core` SDK will connect applications to the AetherMesh peer-to-peer AI network. Eidolon is intended to offer that connection as an optional integration while preserving existing AI providers, authentication, credentials, model routing, and other integrations. This describes the project's direction, not a currently verified network capability.

> **Early Alpha / Proof of Concept.** Consult [Eidolon releases](https://github.com/AetherMesh-AI/Eidolon/releases) for build-specific artifacts, verification scope, and limitations. Eidolon is under active development. The direction described here extends beyond the capabilities currently verified. A published artifact does not imply production readiness or supported installers on every platform.

## The experience we are building

Eidolon centers the interface on agents rather than disposable chat sessions. The intended experience gives each agent a dedicated place in the application, with a persistent conversation that you can return to.

Over time, the goal is to support agents with distinct roles that can work together while preserving their own context. A request should be able to move between the people and agents responsible for it without losing the decisions behind it. You should still be able to see who owns the work, what happened, and where your input is needed.

Memory is part of that direction. An agent should be able to retain useful context, and agents should be able to share relevant knowledge deliberately. Shared memory should not mean giving every agent every conversation.

## Views in the desktop

The desktop brings conversation and planning views together. Their presence in the interface does not mean an organization runtime is connected.

| View | Purpose and current boundary |
| --- | --- |
| Agent conversations | Return to an agent's canonical conversation. Synthetic persistence has bounded native evidence; actual AI conversation flows and reliable first-attempt switching are not yet accepted. |
| Home | Enter an objective and inspect the local planning prototype. Creating an objective does not dispatch work to agents. |
| Objectives | Browse goals and inspect overview, plan, tasks, activity, artifacts, and decisions. Plans are template scaffolds; progress and outcomes are local records, not verified execution. |
| Organization | Inspect example agents, roles, and work relationships. The organization prototype is not a live team manager. |
| Activity | Inspect and filter observable prototype events, not private model reasoning or a certified live execution history. |
| Knowledge | Explore the memory/knowledge prototype. Durable organization knowledge and deliberate cross-agent sharing remain development work. |
| Workspace and capabilities | Existing routes expose artifacts, skills, messaging, scheduling, profiles, and settings. These are integration surfaces, not a claim of end-to-end Eidolon acceptance for every feature or platform. |

Organization example data is fictional. The desktop prototype saves and restores organization records through browser-local storage (`window.localStorage`), separately from canonical conversation storage. This limited local persistence is not backend storage or acceptance of durable organization memory or cross-agent sharing. Local approvals do not grant runtime permissions, and recording an outcome does not prove that work ran.

## Project principles

- Keep conversations continuous. Returning to an agent should feel like picking up where you left off.
- Make agent roles and activity understandable. The interface should help you see which agent you are talking to and what work belongs to it.
- Keep personal and project context appropriately separated. Sharing information between agents should be intentional.
- Keep the user in control. Approvals, access, and consequential actions should have clear boundaries.
- Make ordinary use approachable. Talking to agents should not require keeping a terminal open or managing backend processes by hand.
- Treat reliability as part of the experience. Preserving conversations and recovering safely from failures matter as much as adding features.

These principles guide development; they are not a claim that every permission, memory, or coordination mechanism is already implemented.

## The foundation today

Current development includes a desktop interface organized around agents, canonical per-agent conversations, and a default `~/.eidolon` application data home. Bounded native testing has verified startup, application branding, and persistence of synthetic conversations across restarts.

Local and cross-agent memory remain prototypes. They should not be treated as a mature knowledge system or a security boundary. First-attempt switching to the second agent and process-cleanup verification remain on hold. Complete AI conversation flows and installed-app acceptance still require further verification.

Git-backed updates use this project's repository. A macOS Apple Silicon installed-app fixture verified automatic relaunch after an update, preservation of a synthetic conversation, and automatic recovery after fetch and packaging-command failures. The packaging trial forced the real packaging command to fail; it did not test late bundle corruption or every interrupted-install scenario. These trials used local Git transport and an ad-hoc-signed, non-notarized application, not a distributable release artifact. They establish neither model inference nor Windows/Linux support. This initial alpha is a source release; no notarized installer or cross-platform binary acceptance is claimed.

## Where development is heading

The immediate focus is making the desktop foundation dependable: persistent conversations, predictable agent selection, clear failures, and tested installation and updates.

Beyond that foundation, Eidolon's direction includes richer agent identities and roles, useful persistent memory, deliberate knowledge sharing, and coordinated work with visible ownership and user oversight. These are project goals, not a release schedule or a promise that the current application already provides them.

## Trying Eidolon

Treat development builds as experimental and use disposable data. Do not rely on them as the only copy of important conversations or project information.

Before installing a build, check the [project releases](https://github.com/AetherMesh-AI/Eidolon/releases) for maintainer-published artifacts, build-specific instructions, verification scope, and known limitations. No prerelease or tested installer is established by this documentation update. Source availability alone does not establish that a tested installer or supported update path is available for your platform.

Version-specific changes and installation details belong with the relevant release so this README can remain an overview of the project.

## Contributing

Work that improves reliability, makes agent interaction clearer, or turns a project goal into a tested capability is welcome. Bug reports should describe the expected behavior, what happened, and how to reproduce it without including private conversations or credentials.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [SECURITY.md](SECURITY.md) for security reporting. Development commands and repository instructions are not a guarantee of a supported installation or release. Some other documents and package metadata still describe the original codebase; they should not be read as Eidolon acceptance evidence.

## Origin and license

Eidolon began with the Hermes Agent codebase from Nous Research and is being developed as an independent project. The original attribution and applicable MIT license notices are preserved in [LICENSE](LICENSE) and the source.
