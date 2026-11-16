import logging

import pytest

from akashic.services.directory.authentik import (
    authentik_configured, authentik_keys, fetch_all_authentik, fetch_authentik_person,
)
from akashic.services.directory.records import DirectoryError

URL = "https://auth.test"
TOKEN = "tok-secret-123"


@pytest.fixture
def cfg(rt_set):
    rt_set(authentik_url=URL + "/", authentik_api_token=TOKEN)


def page(results, nxt=0):
    return {"pagination": {"next": nxt, "current": 1}, "results": results}


def u(pk, **kw):
    d = {"pk": pk, "username": f"user{pk}", "name": f"User {pk}",
         "email": f"user{pk}@x.test", "type": "internal", "attributes": {}}
    d.update(kw)
    return d


@pytest.mark.asyncio
async def test_fetch_all_authentik_paginates_users_and_groups(cfg, httpx_mock):
    httpx_mock.add_response(url=f"{URL}/api/v3/core/users/?page=1&page_size=200",
                            json=page([u(1), u(2, type="service_account")], nxt=2))
    httpx_mock.add_response(url=f"{URL}/api/v3/core/users/?page=2&page_size=200",
                            json=page([u(3, type="internal_service_account"), u(4)]))
    httpx_mock.add_response(
        url=f"{URL}/api/v3/core/groups/?page=1&page_size=200&include_users=false",
        json=page([{"pk": "g-uuid", "name": "Staff",
                    "attributes": {"objectSid": "S-1-5-21-1-2-3-513"}}]))
    recs = await fetch_all_authentik()
    assert [(r.kind, r.ref) for r in recs] == [
        ("person", "1"), ("person", "4"), ("group", "g-uuid")]
    p = recs[0]
    assert (p.backend, p.username, p.display_name, p.email) == (
        "authentik", "user1", "User 1", "user1@x.test")
    assert p.groups_status == "none" and p.group_sids == ()
    assert recs[2].display_name == "Staff" and recs[2].sid == "S-1-5-21-1-2-3-513"
    for req in httpx_mock.get_requests():
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"


def test_authentik_keys():
    assert authentik_keys({"objectSid": "S-1-5-21-1-2-3-1001",
                           "distinguishedName": "CN=A,DC=x"}) == (
        "S-1-5-21-1-2-3-1001", "CN=A,DC=x")
    assert authentik_keys({"ldap_uniq": "S-1-5-21-9"}) == ("S-1-5-21-9", None)
    assert authentik_keys({"ldap_uniq": "abc-guid"}) == (None, None)
    assert authentik_keys({}) == (None, None)
    # shared normaliser: lowercase and base64 forms yield the same SID
    assert authentik_keys({"objectSid": "s-1-5-21-1-2-3-1001"})[0] == "S-1-5-21-1-2-3-1001"
    import base64
    raw = bytes([1, 4, 0, 0, 0, 0, 0, 5]) + b"".join(i.to_bytes(4, "little") for i in (1, 2, 3, 4))
    b64 = base64.b64encode(raw).decode()
    assert authentik_keys({"objectSid": b64})[0] == "S-1-5-1-2-3-4"


@pytest.mark.asyncio
async def test_authentik_error_redacts_token(cfg, httpx_mock, caplog):
    httpx_mock.add_response(status_code=401, text=f"bad token {TOKEN}")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(DirectoryError) as ei:
            await fetch_all_authentik()
    assert "401" in str(ei.value)
    assert TOKEN not in str(ei.value) and TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_authentik_connect_error_names_class_only(cfg, httpx_mock):
    import httpx
    httpx_mock.add_exception(httpx.ConnectError(f"failed {TOKEN}"))
    with pytest.raises(DirectoryError) as ei:
        await fetch_all_authentik()
    assert "ConnectError" in str(ei.value) and TOKEN not in str(ei.value)


@pytest.mark.asyncio
async def test_fetch_authentik_person_404_returns_none(cfg, httpx_mock):
    httpx_mock.add_response(url=f"{URL}/api/v3/core/users/7/", status_code=404)
    assert await fetch_authentik_person("7") is None


@pytest.mark.asyncio
async def test_fetch_authentik_person_found(cfg, httpx_mock):
    httpx_mock.add_response(
        url=f"{URL}/api/v3/core/users/7/",
        json=u(7, attributes={"objectSid": "S-1-5-21-1-2-3-7", "distinguishedName": "CN=7"}))
    r = await fetch_authentik_person("7")
    assert (r.ref, r.sid, r.dn) == ("7", "S-1-5-21-1-2-3-7", "CN=7")


def test_authentik_configured(rt_set):
    assert not authentik_configured()
    rt_set(authentik_url=URL, authentik_api_token=TOKEN)
    assert authentik_configured()


@pytest.mark.asyncio
async def test_non_json_200_is_directory_error(cfg, httpx_mock):
    httpx_mock.add_response(text="<html>proxy</html>")
    with pytest.raises(DirectoryError, match="invalid response"):
        await fetch_all_authentik()


@pytest.mark.asyncio
async def test_missing_pk_is_directory_error(cfg, httpx_mock):
    httpx_mock.add_response(json=page([{"username": "nopk"}]))
    with pytest.raises(DirectoryError, match="invalid response"):
        await fetch_all_authentik()


@pytest.mark.asyncio
async def test_person_pk_is_url_quoted(cfg, httpx_mock):
    httpx_mock.add_response(status_code=404)
    assert await fetch_authentik_person("../groups") is None
    path = httpx_mock.get_requests()[0].url.raw_path.decode()
    assert path.startswith("/api/v3/core/users/") and "/groups" not in path.replace("%2F", "")
    assert "%2F" in path
