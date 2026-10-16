# Experimental prepared workspace runtime

These fixed assets run only inside the existing explicitly authorized, digest-pinned
Linux Node container with the reviewed `project_node_runtime/seccomp.json`. They
must never be executed as a host fallback. Source and inventoried prepared
dependencies live together under read-only `/source/root0` (or the exact granted
root alias); all three `.mjs` assets and the exact JSON configuration are mounted
read-only at `/runner`. No package preparation, image pull, shell recipe, package
script, install, credential lookup, TTY, or network grant is provided.

The PID 1 supervisor imports only Node built-ins and fixed protocol code. It
checks cgroup v2 limits, read-only mounts, non-executable bounded scratch, seccomp,
no-new-privileges and its SETUID/SETGID-only capability set. The harness drops to
UID/GID 65534 with no supplementary groups or capabilities. The ready/GO handshake
precedes all prepared dependency and project configuration imports. Ordinary
stdout and stderr are bounded logs; a separate fd 3 carries exactly one ready
frame and one result frame. Missing, extra, unframed, over-limit, signal-ended and
exit-zero-without-result workloads cannot succeed. PID 1 exits to terminate all
descendants; host cleanup confirmation remains separately required.

Before launching the child, the supervisor also compares the prepared bundle's
platform, architecture, libc family, exact Node version and Node module ABI with
the running process, and records those observations in the isolation frame.
Only positively identified glibc/x86-64 Linux is supported. npm version is
preparation metadata, not a claim that an npm runtime was invoked or verified.

The TypeScript recipe calls the inventoried CLI with the actual selected tsconfig,
`--noEmit --noCheck false --incremental false`, and captures its complete reported
non-declaration TypeScript source closure. Every selected source must occur.
The UI recipe calls the inventoried Vitest 4 programmatic API with the actual
Vitest configuration, `configLoader: runner`, and its actual inline `ui` project.
Its extended Vite configuration, setup files, aliases and include patterns are
retained. Fixed resource options impose one isolated fork worker, no watch,
browser, UI, coverage, retries or snapshot updates, and scratch-local caches.
The package installer checks only prepared modules; missing packages fail. Both
the collected and completed module sets must equal the explicit test selection;
every selected module must report at least one passing test and no failure.

Compiler output and Vitest reports remain workload-origin claims, including when
the trusted supervisor transported them faithfully. Host-side exact source,
bundle and profile identity checks, independent review of changes and meaningful
test assertions, and a current complete validation set are still necessary.
These assets do not provide cryptographic proof of assertion honesty.

Control output is limited to 1 MiB without truncation, independently of the
configured log limit. The resource profile currently supports one CPU, 64 PIDs,
and at most 4,096 scratch inodes. Fixed `RAYON_NUM_THREADS=1` and
`UV_THREADPOOL_SIZE=2` bound cooperating native tool pools without relying on
the host's reported CPU count. API/protocol tests do not establish native
compatibility: real offline renderer checking, UI execution, failures and hostile
configuration probes must pass in the pinned sandbox before claiming support.
