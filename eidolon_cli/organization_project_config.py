"""Owner-configured exact project recipes; model text cannot widen these grants."""
from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class OrganizationProjectGrant:
    id: str
    files: tuple[str, ...]
    execution: dict


def parse_project_grants(value, root_count):
    from eidolon_cli.organization_project_runner import normalize_execution_grant
    from tools.organization_file_read import _path_parts, _check_lexical_path
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('organization.project_grants must be a list of at most 8 exact project recipes')
    grants, ids = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'id', 'files', 'execution'}:
            raise ValueError('Project grants require exactly id, files and execution')
        identifier = entry['id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', identifier) or identifier in ids:
            raise ValueError('Project grant IDs must be unique canonical names')
        execution = normalize_execution_grant(entry['execution'])
        policy = asdict(execution)
        root = policy['root']
        if int(root[4:]) >= root_count:
            raise ValueError('Project execution root must name an explicitly configured read root')
        files = entry['files']
        if not isinstance(files, list) or not 1 <= len(files) <= 64 or len(set(files)) != len(files):
            raise ValueError('Project grant files must explicitly list 1–64 unique alias paths')
        for path in files:
            parts = _path_parts(path, absolute=False)
            if len(parts) < 2 or parts[0] != root:
                raise ValueError('Every granted file must belong to the recipe root')
            _check_lexical_path(parts[1:], ())
        grants.append(OrganizationProjectGrant(identifier, tuple(sorted(files)), policy))
        ids.add(identifier)
    return tuple(grants)
