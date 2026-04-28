from akashic.schemas.effective import (
    EffectivePerms,
    EffectivePermsEvaluatedWith,
    EffectivePermsRequest,
    PrincipalRef,
    RightResult,
    ACEReference,
)


def test_effective_perms_round_trip():
    payload = {
        "rights": {
            "read":         {"granted": True,  "by": [{"ace_index": 2, "summary": "user:alice rwx"}]},
            "write":        {"granted": False, "by": []},
            "execute":      {"granted": True,  "by": []},
            "delete":       {"granted": False, "by": []},
            "change_perms": {"granted": False, "by": []},
        },
        "evaluated_with": {
            "model": "posix",
            "principal": {"type": "posix_uid", "identifier": "1000", "name": "alice"},
            "groups": [],
            "caveats": [],
        },
    }
    parsed = EffectivePerms.model_validate(payload)
    assert parsed.rights["read"].granted is True
    assert parsed.rights["read"].by[0].summary == "user:alice rwx"


def test_request_minimal():
    payload = {"principal": {"type": "posix_uid", "identifier": "1000"}}
    parsed = EffectivePermsRequest.model_validate(payload)
    assert parsed.principal.identifier == "1000"
    assert parsed.groups == []
