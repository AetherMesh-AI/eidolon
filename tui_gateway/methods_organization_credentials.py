"""Owner-only catalog RPC. No raw-value read and no agent-text value writes."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
_PREFIX = 'organization.credentials.'
_BASE = {'expectedOrganizationId', 'expectedOwnerId'}
_FIELDS = {
    'catalog': set(),
    'receipt': {'operationId'},
    'cancelOperation': {'operationId', 'confirmed'},
    'configureGrant': {'actorId', 'scope', 'permissions', 'bindings', 'expectedRevision', 'operationId', 'confirmed'},
    'secureInput': {'entryId', 'expectedRevision', 'operationId', 'value'},
    'mutate': {'action', 'entryId', 'expectedRevision', 'operationId', 'metadata', 'inputHandle', 'confirmed'},
}


def _register_credential_method(action, fields):
    @_registry.method(_PREFIX + action)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_credential_service import owner_catalog, credential_agents
        from eidolon_cli.organization_credential_security import CredentialError
        from tui_gateway.organization_credential_transport import owner_home
        transport = current_transport()
        home = owner_home(transport)
        if home is None or home != Path(_eidolon_home).resolve():
            return _err(rid, 4001, 'Credentials require a verified local owner connection', {'reason': 'owner_transport_required'})
        if not isinstance(params, dict) or set(params) != fields | {'expectedOrganizationId', 'expectedOwnerId'}:
            return _err(rid, -32602, 'Unsupported credential parameters')
        try:
            catalog, principal = owner_catalog(home, params['expectedOrganizationId'], params['expectedOwnerId'])
            if action == 'catalog':
                result = catalog.owner_details(principal)
                result['agents'] = credential_agents(home)
                result['secureInputAvailable'] = catalog.backend.status().state == 'available'
                return _ok(rid, result)
            if action == 'configureGrant':
                if params['confirmed'] is not True:
                    raise CredentialError('confirmation_required')
                if params['actorId'] not in {row['identityId'] for row in credential_agents(home)}:
                    raise CredentialError('agent_unavailable')
                # This release has no provider adapters. A use grant cannot imply
                # a generic tool/destination or confer unimplemented authority.
                if params['bindings'] or 'use' in params['permissions']:
                    raise CredentialError('adapter_unavailable')
                return _ok(rid, catalog.configure_grant(principal, actor_id=params['actorId'],
                    scope=params['scope'], permissions=params['permissions'], bindings=params['bindings'],
                    expected_revision=params['expectedRevision'], operation_id=params['operationId']))
            if action == 'cancelOperation':
                if params['confirmed'] is not True:
                    raise CredentialError('confirmation_required')
                return _ok(rid, catalog.cancel_operation(principal, params['operationId']))
            if action == 'receipt':
                return _ok(rid, {'receipt': catalog.receipt(principal, params['operationId'])})
            if action == 'secureInput':
                binding = catalog.value_binding(principal, params['entryId'],
                    expected_revision=params['expectedRevision'], operation_id=params['operationId'])
                handle = catalog.inputs.submit(binding, transport, params['value'])
                return _ok(rid, {'inputHandle': handle, 'expiresIn': 90})
            from tui_gateway.methods_organization_credentials import mutate_credentials
            return _ok(rid, mutate_credentials(catalog, principal, params, transport))
        except CredentialError as error:
            return _err(rid, 5076, str(error), {'reason': error.reason})
        except Exception:
            # Native/provider errors can contain sensitive input. Never log them,
            # expose their string, or hand them to generic dispatcher diagnostics.
            return _err(rid, 5076, 'Organization credentials unavailable', {'reason': 'unavailable'})
    return handler


def mutate_credentials(catalog, principal, params, transport):
    from eidolon_cli.organization_credential_security import CredentialError
    if params['confirmed'] is not True:
        raise CredentialError('confirmation_required')
    action = params['action']
    actions = {
        'create': lambda: catalog.create(principal, params['metadata'], operation_id=params['operationId']),
        'edit_metadata': lambda: catalog.edit_metadata(principal, params['entryId'], params['metadata'],
            expected_revision=params['expectedRevision'], operation_id=params['operationId']),
        'replace_value': lambda: catalog.replace_value(principal, params['entryId'], expected_revision=params['expectedRevision'],
            operation_id=params['operationId'], input_handle=params['inputHandle'], transport=transport),
        'revoke': lambda: catalog.revoke(principal, params['entryId'], expected_revision=params['expectedRevision'], operation_id=params['operationId']),
        'remove': lambda: catalog.revoke(principal, params['entryId'], expected_revision=params['expectedRevision'], operation_id=params['operationId'], remove=True),
    }
    if action not in actions:
        raise CredentialError('invalid_action')
    if (action == 'create' and (params['entryId'] is not None or params['expectedRevision'] is not None)
            or action not in {'create', 'edit_metadata'} and params['metadata'] is not None
            or action != 'replace_value' and params['inputHandle'] is not None):
        raise CredentialError('invalid_parameters')
    return actions[action]()


for _action, _fields in _FIELDS.items():
    _register_credential_method(_action, _fields)


def register(server):
    _registry.install(server)
