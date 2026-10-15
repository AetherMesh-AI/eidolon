# Reviewed runtime assets

The seccomp profile is derived from Moby profiles commit
4b8214fc4971e06172938a1f1554ccff6647ee80, seccomp/default.json.
https://github.com/moby/profiles/tree/4b8214fc4971e06172938a1f1554ccff6647ee80
Its Apache 2.0 license is included. The profile supports native x86-64 only, without compatibility ABIs.
This prevents libseccomp from synthesizing multiplexed compatibility socket rules.
It also removes all allow
rules for ptrace, process_vm_readv, process_vm_writev, pidfd_getfd, kcmp and
process_madvise and socketcall, leaving those operations denied by default. Profile SHA-256:
4445461a8780818be1c3c495c01256a86365356d2cd5d58233fe4d7dab0b5a38.

These assets are used only by an explicitly authorized Node execution grant.
The mandatory native lane verifies the isolation and compatibility-ABI boundaries.
