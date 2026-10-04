# Optional Skills

Bundled optional skills that are **not activated by default**. This collection was inherited from Hermes Agent; original author and license credits are preserved.

These skills ship with the eidolon-agent repository but are not copied to
`~/.eidolon/skills/` during setup. They are discoverable via the Skills Hub:

```bash
eidolon skills browse               # browse all skills, official shown first
eidolon skills browse --source official  # browse only official optional skills
eidolon skills search <query>       # finds optional skills labeled "official"
eidolon skills install <identifier> # copies to ~/.eidolon/skills/ and activates
```

## Why optional?

Some skills are useful but not broadly needed by every user:

- **Niche integrations** — specific paid services, specialized tools
- **Experimental features** — promising but not yet proven
- **Heavyweight dependencies** — require significant setup (API keys, installs)

By keeping them optional, we keep the default skill set lean while still
providing curated, tested, official skills for users who want them.
