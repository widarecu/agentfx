"""`LinearClient` taking its bearer token from a provider (AFX-184).

The fleet must not let the client mint its own token: Linear cancels an
application's earlier tokens when a token is requested with a different
scope set. These tests run the real client against a fake endpoint."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agentfx.integrations import linear_delivery as ld

pytestmark = pytest.mark.unit


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Endpoint:
    """GraphQL endpoint that accepts only the tokens in `valid` and records
    every request. Any call to the token endpoint fails the test."""

    def __init__(self, valid, *, rejection=None):
        self.valid = set(valid)
        # None: answer 401. Otherwise a 200 body's `errors` list.
        self.rejection = rejection
        self.seen: list[str] = []

    def __call__(self, req, timeout=None):
        assert req.full_url != ld.LINEAR_TOKEN_URL, "client must not mint a token"
        auth = req.get_header("Authorization")
        self.seen.append(auth)
        if auth.removeprefix("Bearer ") not in self.valid:
            if self.rejection is not None:
                return _Body(json.dumps({"errors": self.rejection}).encode())
            raise urllib.error.HTTPError(
                req.full_url, 401, "no", {}, io.BytesIO(b'{"errors":[{"message":"x"}]}')
            )
        return _Body(json.dumps({"data": {"viewer": {"id": "v"}}}).encode())


_QUERY = "query { viewer { id } }"


def test_requests_carry_the_provider_token_and_never_mint():
    calls: list[bool] = []

    def provider(force: bool) -> str:
        calls.append(force)
        return "tok-1"

    endpoint = _Endpoint({"tok-1"})
    client = ld.LinearClient(token_provider=provider, opener=endpoint)
    client.gql(_QUERY)
    client.gql(_QUERY)
    assert endpoint.seen == ["Bearer tok-1"] * 2
    assert calls == [False, False]


def test_401_refreshes_through_provider_and_retries_once():
    calls: list[bool] = []

    def provider(force: bool) -> str:
        calls.append(force)
        return "new" if force else "old"

    endpoint = _Endpoint({"new"})
    client = ld.LinearClient(token_provider=provider, opener=endpoint)
    assert client.gql(_QUERY) == {"viewer": {"id": "v"}}
    assert calls == [False, True]
    assert endpoint.seen == ["Bearer old", "Bearer new"]


def test_second_401_is_an_error_not_a_loop():
    calls: list[bool] = []

    def provider(force: bool) -> str:
        calls.append(force)
        return "still-bad"

    endpoint = _Endpoint(set())
    client = ld.LinearClient(token_provider=provider, opener=endpoint)
    with pytest.raises(ld.LinearError, match="401"):
        client.gql(_QUERY)
    assert calls == [False, True]
    assert len(endpoint.seen) == 2


def test_graphql_authentication_error_is_refreshed_too():
    rejection = [{"message": "x", "extensions": {"type": "authentication error"}}]
    endpoint = _Endpoint({"new"}, rejection=rejection)
    client = ld.LinearClient(
        token_provider=lambda force: "new" if force else "old", opener=endpoint
    )
    client.gql(_QUERY)
    assert endpoint.seen == ["Bearer old", "Bearer new"]


def test_other_errors_are_not_retried():
    calls: list[bool] = []

    def provider(force: bool) -> str:
        calls.append(force)
        return "t"

    endpoint = _Endpoint(set(), rejection=[{"message": "bad field"}])
    client = ld.LinearClient(token_provider=provider, opener=endpoint)
    with pytest.raises(ld.LinearError, match="bad field"):
        client.gql(_QUERY)
    assert calls == [False]


def test_provider_failure_surfaces_as_linear_error():
    def provider(force: bool) -> str:
        raise ld.LinearError("no token (reason)")

    client = ld.LinearClient(token_provider=provider, opener=_Endpoint(set()))
    with pytest.raises(ld.LinearError, match="no token"):
        client.gql(_QUERY)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": "lin_x"},
        {"client_id": "id", "client_secret": "secret"},
        {"client_id": "id"},
    ],
)
def test_provider_with_another_credential_raises(kwargs):
    with pytest.raises(ld.LinearError, match="token_provider"):
        ld.LinearClient(token_provider=lambda force: "t", **kwargs)


def test_api_key_401_is_not_retried():
    endpoint = _Endpoint(set())
    client = ld.LinearClient("lin_x", opener=endpoint)
    with pytest.raises(ld.LinearError, match="401"):
        client.gql(_QUERY)
    assert endpoint.seen == ["lin_x"]
