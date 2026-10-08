"""Exercise the real executive stage before tests focused on downstream work."""


def decompose(store, claim=None):
    claim = claim or store.claim_next()
    assert claim is not None and claim['type'] == 'request.decompose'
    context = store.context(claim)
    assert store.finish(claim, decomposition_result(context))
    return claim


def decomposition_result(context):
    objective = context['objective']
    return {'workPackages': [{
        'title': objective['title'], 'description': objective['description'],
        'managerId': objective['managerId'],
        'criterionIndexes': list(range(len(objective['acceptanceCriteria']))),
        'projectIds': [project['id'] for project in objective['projects']],
        'dependsOn': [], 'maxTasks': context['maxTasks'],
    }]}


def claim_after_decomposition(store):
    claim = store.claim_next()
    if claim is not None and claim['type'] == 'request.decompose':
        decompose(store, claim)
        claim = store.claim_next()
    return claim
