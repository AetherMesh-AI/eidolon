# Website

This website is built using [Docusaurus](https://docusaurus.io/), a modern static website generator.

## Installation

```bash
yarn
```

## Local Development

```bash
yarn start
```

This command starts a local development server and opens up a browser window. Most changes are reflected live without having to restart the server.

## Build

```bash
yarn build
```

This command generates static content into the `build` directory and can be served using any static contents hosting service.

## Site identity and deployment

The site uses Eidolon/AetherMesh branding and local catalogs. `EIDOLON_DOCS_URL`
sets its deployment origin; the default is `http://localhost:3000`. No public
Eidolon documentation host is configured or implied. Publishing requires an
explicitly authorized destination and separate deployment verification.

`static/oauth/client-metadata.json` preserves the upstream OAuth client's actual
identity and registered URLs. It is compatibility data, not an AetherMesh OAuth
client registration. Do not change its URLs or claim a new OAuth identity just
because the documentation is hosted elsewhere.

The user-stories page preserves attributed upstream Hermes Agent quotations.
Those reports are not Eidolon acceptance evidence.

## Diagram Linting

CI runs `ascii-guard` to lint docs for ASCII box diagrams. Use Mermaid (````mermaid`) or plain lists/tables instead of ASCII boxes to avoid CI failures.
