# Experimental fixed compiler build

Production dispatch rejects `typescript_build`. This separate foundation tests
bounded compiler-generated JavaScript as data. It does not install dependencies,
run project scripts, execute generated output, write original source folders,
or yet create managed artifact versions in the organization store.

It uses the same pinned compiler inventory, explicit owner-authorized local
Docker runtime, image, seccomp profile, root supervisor and unprivileged workload
as the typecheck adapter. The compiler parses the selected immutable closure.
The fixed compiler host emits only to an in-memory callback. One selected
non-declaration `.ts` file maps to one `.js` output; `.js` import specifiers may
resolve selected `.ts` inputs. Dependency declarations are data-only; dependency
source emission, bundlers, plugins, declaration/source-map output and project
configuration extensions are unsupported.

Limits are 64 outputs, 256 KiB per file, 512 KiB total output bytes and a 1 MiB
control frame. Raw bytes and encoded framing are bounded before transport
allocation. Exact source mappings, canonical paths, sorted descriptors, strict
base64, SHA-256, compiler input closure/options and runtime identities are
verified. Diagnostic output has its own configured bound. Failed, cancelled or
unconfirmed-cleanup runs expose no promotable outputs. No writable host mount
is used. UTF-8 byte ordering is shared across the Node/Python boundary.

Native proof and independent review must precede integration. The follow-on
must atomically retain exact verified outputs as managed artifact versions,
bind them to an immutable reviewed execution receipt, and separately verify
`project_build` completion. Build evidence cannot replace functional tests.
