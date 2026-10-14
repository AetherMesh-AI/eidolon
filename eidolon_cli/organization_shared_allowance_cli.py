"""Review and explicitly opt in to one owner-declared installation allowance.

Run from the installation root home to edit. Named profiles may only show their
scoped projection. JSON files contain the exact versioned review/apply envelopes;
no command accepts another profile, a root path, or credentials as authority.
This ledger is an internal allowance, not an external account quota or invoice.
"""
import argparse
import json
from pathlib import Path


def main(argv=None):
    from eidolon_constants import get_process_eidolon_home
    from eidolon_cli.organization_shared_allowance import resolve_authority
    from eidolon_cli.organization_shared_allowance_controls import (
        shared_allowance, preview_shared_allowance, apply_shared_allowance,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    show = commands.add_parser('show', help='Read the served home allowance without initializing storage')
    show.add_argument('--expected-organization-id')
    for operation in ('preview', 'apply'):
        command = commands.add_parser(operation)
        command.add_argument('--action', choices=('configuration', 'observation'), required=True)
        command.add_argument('--file', type=Path, required=True, help='Exact JSON review envelope')
        if operation == 'apply':
            command.add_argument('--confirm', action='store_true', required=True,
                                 help='Apply this exact previewed candidate')
    args = parser.parse_args(argv)
    # Ignore ContextVar profile routing inherited from callers. The process home
    # is the immutable CLI launch authority, just as _eidolon_home is for RPC.
    home = get_process_eidolon_home()
    try:
        if args.command == 'show':
            expected_id = args.expected_organization_id or resolve_authority(home)['servedIdentity']['organizationId']
            result = shared_allowance(home, expected_id)
        else:
            if args.file.stat().st_size > 200_000:
                raise ValueError('Shared allowance request exceeds its 200000-byte bound')
            params = json.loads(args.file.read_text(encoding='utf-8'))
            operation = preview_shared_allowance if args.command == 'preview' else apply_shared_allowance
            result = operation(home, args.action, params)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
