"""Owner-only secure preparation; the model receives only a non-bearer reference."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
_FIELDS = {
    'replacementTargets': {'entryId', 'expectedRevision'},
    'prepareReplacement': {'entryId', 'expectedRevision', 'actorId', 'requestId', 'generation',
                           'expectedGrantRevision', 'operationId', 'value', 'confirmed'},
    'replacementStatus': {'operationId'},
    'cancelReplacement': {'operationId', 'confirmed'},
}


def _register(action, fields):
    @_registry.method('organization.credentials.' + action)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_credential_handoff import handoffs
        from eidolon_cli.organization_credential_security import CredentialError
        from eidolon_cli.organization_credential_service import owner_catalog
        from eidolon_cli.organization_metadata import read_descriptor
        from tui_gateway.organization_credential_transport import owner_home, owner_generation
        transport = current_transport()
        home, generation = owner_home(transport), owner_generation(transport)
        if home is None or generation is None or home != Path(_eidolon_home).resolve():
            return _err(rid, 4001, 'Credentials require a verified local owner connection', {'reason': 'owner_transport_required'})
        if not isinstance(params, dict) or set(params) != {'expectedOrganizationId', 'expectedOwnerId'} | fields:
            return _err(rid, -32602, 'Unsupported credential parameters')
        try:
            catalog, owner = owner_catalog(home, params['expectedOrganizationId'], params['expectedOwnerId'])

            def transport_guard():
                if owner_home(transport) != home or owner_generation(transport) is not generation:
                    raise CredentialError('owner_transport_required')
                metadata = read_descriptor(home / 'organization' / 'state.db').get('metadata')
                if (not metadata or metadata['organizationId'] != owner.organization_id
                        or metadata['owner']['ownerId'] != owner.actor_id):
                    raise CredentialError('identity_changed')

            transport_guard()
            if action == 'replacementTargets':
                result = handoffs.targets(catalog, owner, params['entryId'], params['expectedRevision'])
                return _ok(rid, {'targets': result})
            if action == 'replacementStatus':
                return _ok(rid, handoffs.status(catalog, owner, params['operationId']))
            if params['confirmed'] is not True:
                raise CredentialError('confirmation_required')
            if action == 'cancelReplacement':
                return _ok(rid, handoffs.cancel(catalog, owner, params['operationId']))
            result = handoffs.prepare(catalog, owner, params['entryId'], params['expectedRevision'],
                params['actorId'], params['requestId'], params['generation'], params['operationId'],
                transport, params['value'], transport_guard, params['expectedGrantRevision'])
            return _ok(rid, result)
        except CredentialError as error:
            return _err(rid, 5076, str(error), {'reason': error.reason})
        except Exception:
            return _err(rid, 5076, 'Organization credentials unavailable', {'reason': 'unavailable'})
    return handler


for _action, _fields in _FIELDS.items():
    _register(_action, _fields)


def register(server):
    _registry.install(server)
