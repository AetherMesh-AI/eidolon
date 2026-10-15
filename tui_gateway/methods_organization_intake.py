"""Authenticated, profile-scoped request intake. Reads do not dispatch work."""
from .method_ctx import HandlerRegistry
from .methods_organization import _organization_method, _organization_params

_registry = HandlerRegistry()


def _view(value):
    from eidolon_cli.profiles import get_active_profile_name
    return {**value, 'profile': get_active_profile_name()}


@_organization_method('organization.intake.submit', registry=_registry)
def _submit(params, service):
    _organization_params(params, {'text', 'idempotencyKey', 'ownerPriority'})
    return _view(service().intake.submit(params.get('text'), idempotency_key=params.get('idempotencyKey'),
                                        owner_priority=params.get('ownerPriority')))


@_organization_method('organization.intake.read', registry=_registry)
def _read(params, service):
    _organization_params(params, {'intakeId'})
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.organization_intake_observation import read_intakes
    if not params.get('intakeId'):
        raise ValueError('intakeId is required')
    return _view(read_intakes(get_eidolon_home(), intake_id=params['intakeId']))


@_organization_method('organization.intake.list', registry=_registry)
def _list(params, service):
    _organization_params(params, set())
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.organization_intake_observation import read_intakes
    return _view({'items': [_view(item) for item in read_intakes(get_eidolon_home())]})


@_organization_method('organization.intake.recover', registry=_registry)
def _recover(params, service):
    _organization_params(params, set())
    return _view({'items': [_view(item) for item in service().intake.recover()]})


@_organization_method('organization.intake.answer', registry=_registry)
def _answer(params, service):
    _organization_params(params, {'intakeId', 'text', 'idempotencyKey'})
    if not params.get('intakeId'):
        raise ValueError('intakeId is required')
    return _view(service().intake.submit(params.get('text'), idempotency_key=params.get('idempotencyKey'),
                                        parent_intake_id=params['intakeId']))


def register(server):
    _registry.install(server)
