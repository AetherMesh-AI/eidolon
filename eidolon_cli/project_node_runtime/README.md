# Reviewed runtime assets (experimental, not dispatched)

The seccomp profile is derived from Moby profiles commit
4b8214fc4971e06172938a1f1554ccff6647ee80, seccomp/default.json.
https://github.com/moby/profiles/tree/4b8214fc4971e06172938a1f1554ccff6647ee80
Its Apache 2.0 license is included. The only semantic change removes all allow
rules for ptrace, process_vm_readv, process_vm_writev, pidfd_getfd, kcmp and
process_madvise and socketcall, leaving those operations denied by default. Profile SHA-256:
72d5f75b6d9876f1c9cad2a170a060420c83184a7c4c0022502fbc5c6308ef52.

These assets are experimental and not enabled by any execution grant. Native
adversarial proof and independent implementation review are required first.
