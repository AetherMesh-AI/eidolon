# Eidolon CLI Reference

Live sources when anything looks stale: `eidolon --help`, `eidolon <command> --help`,
/reference/cli-commands

### Global Flags

```
eidolon [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
eidolon chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
eidolon setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
eidolon model                Interactive model/provider picker
eidolon fallback [add|remove|list]  Fallback provider chain
eidolon config [show|edit|get|set|unset|path|env-path|check|migrate]
eidolon login / logout       OAuth sign-in / clear stored auth
eidolon doctor [--fix]       Check dependencies and config
eidolon status [--all]       Component status
```

### Tools & Skills

```
eidolon tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

eidolon skills list|browse|search QUERY|inspect ID
eidolon skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
eidolon skills config        Enable/disable skills per platform
eidolon skills check|update|uninstall|publish PATH
eidolon skills tap add REPO  Add a GitHub repo as a skill source
eidolon bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
eidolon mcp add NAME (--url or --command) | remove | list | test NAME
eidolon mcp catalog | install NAME     Curated catalog install
eidolon mcp configure NAME             Toggle tool selection
eidolon mcp serve                      Run Eidolon as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
eidolon gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `eidolon photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: /user-guide/messaging/

### Sessions

```
eidolon sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
eidolon cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
eidolon webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
eidolon profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
eidolon profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
eidolon auth                 Interactive credential manager
eidolon auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
eidolon auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
eidolon desktop / gui        Native desktop app
eidolon dashboard            Web admin panel + embedded chat (--stop / --status)
eidolon proxy                OpenAI-compatible local proxy backed by an OAuth provider
eidolon portal               Quick setup / sign in via Nous Portal
eidolon kanban <verb>        Multi-agent work-queue board
eidolon project              Named multi-folder workspaces
eidolon skin list|use|set    Switch/tweak skins (see references/themes.md)
eidolon pets <verb>          Pet mascots (see references/petdex.md)
eidolon memory setup|status|off|reset   Memory provider
eidolon secrets bitwarden|onepassword   External secret stores
eidolon moa                  Mixture-of-Agents slots
eidolon hooks / security / backup / import / checkpoints / console
eidolon logs [-f] [errors]   View agent/error logs
eidolon send                 One-off message through a gateway platform
eidolon pairing / plugins / insights / journey / computer-use
eidolon acp                  ACP server (IDE integration)
eidolon completion bash|zsh|fish
eidolon update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `eidolon photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `eidolon config edit` · [Configuration docs](/user-guide/configuration) |
| Tools / toolsets | `eidolon tools list` · [Tools reference](/reference/tools-reference) |
| Skills catalog | `eidolon skills browse` · [Skills catalog](/reference/skills-catalog) |
| Provider setup | `eidolon model` · [Providers guide](/integrations/providers) |
| Env variables | `eidolon config env-path` · [Env vars reference](/reference/environment-variables) |
| Gateway logs | `~/.eidolon/logs/gateway.log` (or `eidolon logs`) |
| Sessions | `eidolon sessions browse` (reads state.db) |
