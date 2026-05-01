"""Filter-grammar round-tripping and sink emission.

The TS side has its own tests for the deserialize/serialize symmetry —
this file owns the Python contracts: validation, sink output shape,
and the principal-predicate signal the Search router will key off of.
"""
import pytest
from pydantic import ValidationError

from akashic.services.filter_grammar import (
    ExtensionPred,
    MimePred,
    MtimePred,
    OwnerPred,
    PathPred,
    PrincipalPred,
    SizePred,
    SourcePred,
    has_meili_inexpressible_predicate,
    has_principal_predicate,
    parse,
    serialize,
    to_meili,
    to_sqlalchemy,
)


# ── Round-trips ────────────────────────────────────────────────────────────


def test_round_trip_single_extension():
    preds = [ExtensionPred(kind="extension", value="pdf")]
    assert parse(serialize(preds)) == preds


def test_round_trip_multiple_predicates():
    preds = [
        ExtensionPred(kind="extension", value="pdf"),
        PrincipalPred(kind="principal", value="sid:S-1-5-21-1-2-3", right="read"),
        SizePred(kind="size", op="gte", value=1024),
        MtimePred(kind="mtime", op="lte", value="2026-04-30T12:00:00"),
    ]
    decoded = parse(serialize(preds))
    assert len(decoded) == 4
    assert decoded[0] == preds[0]
    assert decoded[1].right == "read"
    assert decoded[2].op == "gte"
    assert decoded[3].value == "2026-04-30T12:00:00"


def test_round_trip_unicode_owner():
    """Path-like values (slashes, dots, colons, unicode) must survive."""
    preds = [OwnerPred(kind="owner", value="DOMAIN\\üser-ñame")]
    assert parse(serialize(preds))[0].value == preds[0].value


def test_empty_list_round_trips_through_empty_string():
    assert serialize([]) == ""
    assert parse("") == []


# ── Defensive parsing ──────────────────────────────────────────────────────


def test_parse_invalid_base64_raises():
    with pytest.raises(ValueError, match="invalid filter grammar"):
        parse("!!! not base64 !!!")


def test_parse_valid_b64_but_not_predicate_list_raises():
    # b64 of `{"hello":"world"}` — valid JSON, wrong shape.
    encoded = "eyJoZWxsbyI6IndvcmxkIn0"
    with pytest.raises(ValueError, match="invalid filter grammar"):
        parse(encoded)


def test_principal_predicate_rejects_unknown_right():
    with pytest.raises(ValidationError):
        PrincipalPred(kind="principal", value="sid:X", right="execute")  # type: ignore[arg-type]


# ── to_meili ────────────────────────────────────────────────────────────────


def test_to_meili_extension():
    out = to_meili([ExtensionPred(kind="extension", value="pdf")])
    assert out == 'extension = "pdf"'


def test_to_meili_principal_right_maps_to_viewable_field():
    cases = [
        ("read", 'viewable_by_read = "sid:S-1-5"'),
        ("write", 'viewable_by_write = "sid:S-1-5"'),
        ("delete", 'viewable_by_delete = "sid:S-1-5"'),
    ]
    for right, expected in cases:
        out = to_meili([PrincipalPred(kind="principal", value="sid:S-1-5", right=right)])
        assert out == expected


def test_to_meili_size_range_anded():
    preds = [
        SizePred(kind="size", op="gte", value=1024),
        SizePred(kind="size", op="lte", value=1048576),
    ]
    assert to_meili(preds) == "size_bytes >= 1024 AND size_bytes <= 1048576"


def test_to_meili_escapes_quotes_in_values():
    out = to_meili([OwnerPred(kind="owner", value='alice "the great"')])
    assert out == 'owner_name = "alice \\"the great\\""'


def test_to_meili_empty_list_is_empty_string():
    assert to_meili([]) == ""


# ── to_sqlalchemy ───────────────────────────────────────────────────────────


def test_to_sqlalchemy_extension_returns_one_clause():
    clauses = to_sqlalchemy([ExtensionPred(kind="extension", value="pdf")])
    assert len(clauses) == 1
    # Force compile so we know it's a valid expression, not just a tuple.
    assert "extension" in str(clauses[0].compile(compile_kwargs={"literal_binds": True}))


def test_to_sqlalchemy_skips_principal_predicates():
    """Principal predicates require Phase 4 columns; until then they're
    skipped silently. has_principal_predicate() is the signal that the
    caller should take the Meili path."""
    preds = [
        ExtensionPred(kind="extension", value="pdf"),
        PrincipalPred(kind="principal", value="sid:S-1-5", right="read"),
    ]
    clauses = to_sqlalchemy(preds)
    assert len(clauses) == 1  # only the extension clause
    assert has_principal_predicate(preds) is True


def test_to_sqlalchemy_size_range_emits_two_clauses():
    preds = [
        SizePred(kind="size", op="gte", value=1024),
        SizePred(kind="size", op="lte", value=1048576),
    ]
    clauses = to_sqlalchemy(preds)
    assert len(clauses) == 2


def test_to_sqlalchemy_source_uuid_string_is_validated():
    """UUID validation happens in to_sqlalchemy, not at parse time, so a
    bad UUID surfaces only when actually used in a query."""
    preds = [SourcePred(kind="source", value="not-a-uuid")]
    with pytest.raises(ValueError):
        to_sqlalchemy(preds)


# ── has_principal_predicate ────────────────────────────────────────────────


def test_has_principal_predicate_false_for_no_principal():
    assert has_principal_predicate([ExtensionPred(kind="extension", value="pdf")]) is False
    assert has_principal_predicate([]) is False


def test_has_principal_predicate_true_when_present():
    assert (
        has_principal_predicate([
            PrincipalPred(kind="principal", value="sid:X", right="write"),
        ])
        is True
    )


# ── PathPred (Phase A) ─────────────────────────────────────────────────────


def test_path_predicate_round_trip():
    preds = [PathPred(kind="path", value="/Reports/Q3")]
    decoded = parse(serialize(preds))
    assert len(decoded) == 1 and decoded[0].value == "/Reports/Q3"


def test_path_predicate_to_meili_skipped():
    """Meili can't express path-prefix today; to_meili drops the
    predicate and the Search router falls through to SQL via
    has_meili_inexpressible_predicate()."""
    preds = [
        PathPred(kind="path", value="/x"),
        ExtensionPred(kind="extension", value="pdf"),
    ]
    out = to_meili(preds)
    # The extension predicate still emits.
    assert out == 'extension = "pdf"'
    assert has_meili_inexpressible_predicate(preds) is True


def test_path_predicate_to_sqlalchemy_emits_match_or_descendant():
    clauses = to_sqlalchemy([PathPred(kind="path", value="/Reports")])
    assert len(clauses) == 1
    sql = str(clauses[0].compile(compile_kwargs={"literal_binds": True}))
    # Matches the directory itself OR any descendant.
    assert "/Reports" in sql
    assert "LIKE" in sql or "like" in sql


def test_path_predicate_root_or_empty_is_no_op():
    """`/` or empty string would match everything — emit no clause."""
    assert to_sqlalchemy([PathPred(kind="path", value="/")]) == []
    assert to_sqlalchemy([PathPred(kind="path", value="")]) == []


def test_has_meili_inexpressible_false_for_basic_predicates():
    assert has_meili_inexpressible_predicate([
        ExtensionPred(kind="extension", value="pdf"),
        SizePred(kind="size", op="gte", value=1),
    ]) is False
