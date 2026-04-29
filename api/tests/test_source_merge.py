"""Phase 2 — unit tests for the connection_config merge helper.

The merge guards against the UI re-saving a scrubbed config and
overwriting the real secret with `"***"`. These tests pin down each
edge case so a future refactor can't quietly break the guarantee.
"""
from __future__ import annotations

from akashic.services.source_merge import field_diff, merge_connection_config


def test_secret_masked_value_preserves_existing():
    existing = {"host": "h", "password": "real-secret"}
    incoming = {"host": "h", "password": "***"}
    out = merge_connection_config(existing, incoming)
    assert out == {"host": "h", "password": "real-secret"}


def test_secret_real_new_value_overwrites():
    existing = {"host": "h", "password": "old"}
    incoming = {"host": "h", "password": "new-real"}
    out = merge_connection_config(existing, incoming)
    assert out["password"] == "new-real"


def test_non_secret_field_overwrites_normally():
    existing = {"host": "old", "password": "p"}
    incoming = {"host": "new"}
    out = merge_connection_config(existing, incoming)
    assert out == {"host": "new", "password": "p"}


def test_partial_incoming_keeps_unmentioned_fields():
    existing = {"host": "h", "port": 22, "password": "p"}
    incoming = {"port": 2222}
    out = merge_connection_config(existing, incoming)
    assert out == {"host": "h", "port": 2222, "password": "p"}


def test_new_secret_key_with_real_value_added():
    existing = {"host": "h"}
    incoming = {"host": "h", "secret_access_key": "AKIA…"}
    out = merge_connection_config(existing, incoming)
    assert out["secret_access_key"] == "AKIA…"


def test_keytab_path_treated_as_secret():
    """`keytab_path` doesn't contain "password" but DOES match the
    pattern via the "key" token in _SECRET_KEYS — confirm coverage."""
    existing = {"keytab_path": "/etc/krb5.keytab"}
    incoming = {"keytab_path": "***"}
    out = merge_connection_config(existing, incoming)
    assert out["keytab_path"] == "/etc/krb5.keytab"


def test_empty_existing_with_incoming_keeps_real_secret():
    out = merge_connection_config(None, {"password": "real"})
    assert out == {"password": "real"}


def test_empty_inputs_yield_empty_dict():
    assert merge_connection_config(None, None) == {}
    assert merge_connection_config({}, {}) == {}


def test_diff_skips_unchanged_fields():
    before = {"host": "h", "port": 22}
    after = {"host": "h", "port": 22}
    assert field_diff(before, after) == {}


def test_diff_reports_changed_non_secret():
    before = {"host": "old", "port": 22}
    after = {"host": "new", "port": 22}
    d = field_diff(before, after)
    assert d == {"host": {"before": "old", "after": "new"}}


def test_diff_redacts_secret_changes():
    before = {"password": "old-real"}
    after = {"password": "new-real"}
    d = field_diff(before, after)
    # Both sides redacted to state tokens, never literal values.
    assert d == {"password": {"before": "<set>", "after": "<set>"}}


def test_diff_secret_set_from_empty():
    d = field_diff({"password": ""}, {"password": "new"})
    assert d == {"password": {"before": "<empty>", "after": "<set>"}}


def test_diff_secret_cleared():
    d = field_diff({"password": "old"}, {"password": ""})
    assert d == {"password": {"before": "<set>", "after": "<empty>"}}


def test_diff_added_and_removed_keys():
    before = {"a": 1}
    after = {"b": 2}
    d = field_diff(before, after)
    assert d == {
        "a": {"before": 1, "after": None},
        "b": {"before": None, "after": 2},
    }
