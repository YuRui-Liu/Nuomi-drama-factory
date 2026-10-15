"""Pure validation for structured, user-reviewable ideation candidates."""

_ROOT_FIELDS = {'schema_version', 'candidates'}
_CANDIDATE_FIELDS = {
    'id', 'title', 'premise', 'protagonist_goal', 'conflict', 'hook', 'expansion',
}
_BODY_FIELDS = ('title', 'premise', 'protagonist_goal', 'conflict', 'hook', 'expansion')
_MAX_BODY_LENGTH = 30000


def validate_candidates(payload):
    """Validate schema v1 and return trimmed candidate dictionaries.

    The input is treated as already-parsed JSON. It is never modified.
    """
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise ValueError('payload must contain exactly schema_version and candidates')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
        raise ValueError('schema_version must be integer 1')

    candidates = payload['candidates']
    if not isinstance(candidates, list) or len(candidates) != 3:
        raise ValueError('candidates must contain exactly three items')

    result = []
    seen_ids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_FIELDS:
            raise ValueError('each candidate must contain exactly the required fields')

        identifier = candidate['id']
        if not isinstance(identifier, str):
            raise ValueError('candidate id must be a non-empty string')
        identifier = identifier.strip()
        if not identifier or identifier in seen_ids:
            raise ValueError('candidate ids must be non-empty and unique')
        seen_ids.add(identifier)

        normalized = {'id': identifier}
        body_length = 0
        for field in _BODY_FIELDS:
            value = candidate[field]
            if not isinstance(value, str):
                raise ValueError(f'{field} must be a non-empty string')
            value = value.strip()
            if not value:
                raise ValueError(f'{field} must be a non-empty string')
            body_length += len(value)
            normalized[field] = value

        if body_length > _MAX_BODY_LENGTH:
            raise ValueError('candidate body text exceeds 30000 characters')
        result.append(normalized)

    return result
