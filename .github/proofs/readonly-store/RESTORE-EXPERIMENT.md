# Bounded restore-only experiment

Status: prepared for independent review, not a native result or guaranteed fix.
This overlay derives only from source manifest
`e7752bc60c5ef78877021767b16977f870f7a2471227a4edb4b08abdc5e4c743`.
All prior source freezes and hosted evidence remain separate and unchanged.

## Exact change and scope

Only `native_permissions.py`'s Windows binding and restore method change:

- Bind `SetFileSecurityW` as `BOOL(LPCWSTR, DWORD, void*)`, using the existing
  `advapi32` binding with `use_last_error=True`.
- Allocate the same local `create_string_buffer(saved["descriptor"])` as before.
  The saved self-relative descriptor bytes are copied unchanged; the allocation's
  extra terminal NUL is outside the descriptor's own encoded extent.
- Retain `_dacl(descriptor)` and its native present/non-null DACL validation.
  A failed query, absent DACL or null DACL prevents the write.
- Call `SetFileSecurityW(str(path), DACL_SECURITY_INFORMATION, descriptor)` once.
  The local descriptor buffer stays strongly referenced through the synchronous
  validation and write calls. `_ok` accepts nonzero BOOL and raises the captured
  Windows last-error on zero. This is not SetNamedSecurityInfo's DWORD convention.
- Do not add PROTECTED/UNPROTECTED `SECURITY_INFORMATION` selectors. The only
  selector is 4. Original protected/auto-inherited bits remain untouched in the
  supplied saved descriptor; their actual restoration is an experimental result.

Capture (OWNER/GROUP/DACL only), all restriction calls, ownership/path/identity
fences, child-first restriction, parent-first restoration, every-target cleanup,
error ordering, exact byte assertion, diagnostic hook, successful receipt fields,
verifier, hosted case lists and runner remain identical to the frozen parent.
No owner/group/SACL selector, token privilege operation, external path, fallback,
retry, extra native setup, baseline normalization, control-bit clearing, masking,
or assertion relaxation is introduced. If a required privilege is absent, fail
and report it; never enable a privilege or use an alternate security API.

## What the original diagnostic observed

Run 39366798690 produced four valid before/after comparisons:

- Windows 3.11 directory and state.db: 0x8004 to 0x8404, length 164 each.
- Windows 3.12 directory: 0x9004 to 0x9404, length 148.
- Windows 3.12 state.db: 0x8004 to 0x8404, length 148.

Only control bit 0x0400 (SE_DACL_AUTO_INHERITED) differed. Owner/group bytes,
component offsets, DACL bytes, ordered ACE bytes, revision and Sbz1 were equal.
Header and reported component spans cover the complete compared descriptors.
The Windows 3.12 directory was already protected. The baseline and final
snapshots do not identify the intermediate operation that first changed the bit.
This is a real control-field mismatch, and the full-byte restoration failed.
The observations do not establish effective-access equivalence.

## Documented contracts versus unproven behavior

Microsoft's [SetNamedSecurityInfoW contract](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow)
accepts component pointers, not a complete descriptor header. Its DACL operation
requires WRITE_DAC or ownership and propagates inheritable ACEs. Microsoft's
[inheritance model documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/automatic-propagation-of-inheritable-aces)
says SetNamedSecurityInfo/SetSecurityInfo impose the current inheritance model;
converted objects acquire AUTO_INHERITED flags. These contracts align with the
observed bit addition but do not identify its stage in this fixture.

[SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)
selectors and SECURITY_DESCRIPTOR_CONTROL bits are different namespaces. There
is no documented AUTO_INHERITED selector. Some selectors apply only to
SetNamedSecurityInfo. Adding the numeric 0x0400 control bit to a selector would
not be a supported way to request control restoration.

[SetSecurityDescriptorControl](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-setsecuritydescriptorcontrol)
modifies a supplied in-memory descriptor, not the stored filesystem object.
Passing only that descriptor's DACL to SetNamedSecurityInfo loses its header.

[SetFileSecurityW](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-setfilesecurityw)
is a documented file/directory API accepting a descriptor and component selector.
Microsoft marks it obsolete and recommends SetNamedSecurityInfo instead. Its
DACL-only operation requires WRITE_DAC or ownership; directory changes through
it are not inherited by children. It returns BOOL and uses GetLastError on
failure. Documentation does not promise preservation of AUTO_INHERITED,
protected state, ACE ordering, or arbitrary serialized descriptor bytes.

[Self-relative descriptor documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/absolute-and-self-relative-security-descriptors)
permits supplying absolute or self-relative descriptors unless an API says
otherwise. This establishes input representation, not a byte-round-trip promise.
[SetKernelObjectSecurity](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-setkernelobjectsecurity)
explicitly advises against filesystem objects and is not an alternate here.

Thus this API change is only a narrowly scoped experiment. A successful API
return does not establish restoration. The existing full-byte readback assertion
remains decisive, and failures remain failures with the closed diagnostic marker.

## Portable controls and native limits

Run `python test_windows_restore_experiment.py --receipt evidence/controls.json`.
The suite has 30 restore-specific controls and 39 unchanged inherited descriptor
controls. The inherited original-module AST pin is deliberately not modified;
its single case is replaced by an exact parent-relative AST check that allows
only the new binding and exact reviewed restore body. All other original source
files are checked against the parent manifest.

Portable coverage includes exact types/arguments; nonzero BOOL and false/error
semantics; real local ctypes buffer bytes, terminator, same-buffer validation and
lifetime; one mutating call; absent/null/query-failure refusal; allocation and
wrapper exceptions; all four protected/AUTO_INHERITED bit combinations; changed
separate saved-control metadata; ordered explicit/inherited ACEs; offset layouts,
aliasing and padding; root/path/owner/link/refusal fences; every-target restore and
check order; partial restriction failure; interrupted body and grouped cleanup
errors; unchanged full-byte mismatch; and preservation of a failed fixture.
Every Windows function is a Python fake. No Windows DLL or security API executes,
and no filesystem permissions change. Local ctypes memory operations do run;
these controls cannot establish Windows ABI or filesystem behavior.

Native scope stays on the original actual-baseline cases and unchanged 27/34
inventory. No additional baseline preparation or protected/unprotected setup is
added. For any later authorized run, the exact snapshot readback is mandatory.
A mismatch may reveal controls through the existing diagnostic. Successful
receipts contain descriptor hashes only; they do not expose control words, so
coverage of protected/unprotected or AUTO_INHERITED states is unavailable from
successful receipts alone. Earlier run states must not be assigned to a new run.
Any unobserved state remains an explicit coverage limit. No successful observed
baseline proves universal round-trip support. Publication and native execution
require separate independent source/pointer/error/fence and exact-wrapper review.
