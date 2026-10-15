"""Owner-only catalogue controls for the current HERMES_HOME profile.

configure accepts a credential-free JSON object containing model_routes and
model_assignments. It does not alter provider connections, prices or grants,
start execution, or probe providers. Changes fence already running claims.
"""
import argparse
import json
from pathlib import Path

from eidolon_constants import get_eidolon_home


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    observations = commands.add_parser('observations')
    observations.add_argument('--limit', type=int, default=200)
    configure = commands.add_parser('configure')
    configure.add_argument('--file', type=Path, required=True)
    configure.add_argument('--expected-generation', type=int, required=True)
    configure.add_argument('--idempotency-key', required=True)
    args = parser.parse_args(argv)
    home = get_eidolon_home()
    try:
        path = home / 'organization' / 'state.db'
        if args.command == 'observations':
            from eidolon_cli.organization_routing_observations import routing_observations
            print(json.dumps(routing_observations(path, limit=args.limit), sort_keys=True))
            return 0
        from eidolon_cli.organization_store import OrganizationStore
        if not path.is_file():
            raise ValueError('No existing organization ledger is available in this profile')
        store = OrganizationStore(path)
        store.reload_profile_configuration(home)
        if args.command == 'show':
            result = store.model_routing_configuration()
        else:
            if args.file.stat().st_size > 200_000:
                raise ValueError('Routing configuration file exceeds its 200000-byte bound')
            configuration = json.loads(args.file.read_text(encoding='utf-8'))
            result = store.configure_model_routing(configuration, expected_generation=args.expected_generation,
                idempotency_key=args.idempotency_key, home=home)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
