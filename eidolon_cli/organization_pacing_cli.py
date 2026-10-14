"""Owner terminal controls for a profile's advisory budget observations.

No model proposal or tool route exposes this mutation. This command never starts
execution or probes a provider, and cannot change objective admission ceilings.
"""
import argparse
import json

from eidolon_constants import get_eidolon_home
from eidolon_cli.organization_observation import read_observation
from eidolon_cli.organization_store import OrganizationStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    show = commands.add_parser('show', help='Read pacing and the latest owner observation without starting execution')
    show.add_argument('objective')
    observe = commands.add_parser('observe', help='Record an owner-observed remaining percentage; never replenishes budget')
    observe.add_argument('objective')
    observe.add_argument('--remaining-percent', type=float, required=True)
    observe.add_argument('--as-of', required=True, help='ISO timestamp with timezone')
    observe.add_argument('--expires-at', required=True, help='ISO timestamp with timezone, at most seven days after as-of')
    observe.add_argument('--reset-at', help='Unconfirmed expected reset; never replenishes allowance')
    observe.add_argument('--expected-revision', type=int, required=True)
    observe.add_argument('--idempotency-key', required=True)
    args = parser.parse_args(argv)
    home = get_eidolon_home()
    try:
        if args.command == 'show':
            result = read_observation(home)
            if result['state'] != 'available':
                raise ValueError('No current organization ledger is available in this profile')
            objective = next((row for row in result['snapshot']['objectives'] if row['id'] == args.objective), None)
            if objective is None:
                raise ValueError('Objective not found in this profile')
            result = objective['usage'].get('adaptivePacing', {'enabled': False, 'mode': 'off', 'observation': None})
        else:
            path = home / 'organization' / 'state.db'
            if not path.is_file():
                raise ValueError('No existing organization ledger is available in this profile')
            store = OrganizationStore(path)
            # File-backed adoption reads configuration under the ledger writer,
            # preserving the same stale-host/invalid-file fence as the service.
            store.reload_profile_configuration(home)
            result = store.record_budget_observation(args.objective, remaining_percent=args.remaining_percent,
                as_of=args.as_of, expires_at=args.expires_at, reset_at=args.reset_at,
                expected_revision=args.expected_revision, idempotency_key=args.idempotency_key)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
