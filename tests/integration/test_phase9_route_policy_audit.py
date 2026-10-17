
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-901 — Policy audit for the two new topic-authoring routes.

Kept in a separate file (instead of editing the existing phase3 audit) so
the SRA-509 baseline stays clean while the Phase 9 routes are not yet
mounted. The two assertions here pin the security decision made by the
ticket: both routes require an authenticated user and the
``limit_authenticated`` token-bucket rate limit; the GET scopes
references to the caller via the URL's ``entry_id``.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from sra.api.rate_limit import limit_authenticated
from sra.auth.dependencies import require_user

pytestmark = pytest.mark.phase9

EXPECTED_POLICIES = {
    ("POST", "/api/v1/topics"): "user",
    ("GET", "/api/v1/entries/{entry_id}/references"): "user",
}


def test_phase9_routes_have_user_authentication_and_rate_limit(client):
    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    seen = set()
    for route in iter_route_contexts(client.app.routes):
        assert route.path is not None
        if not isinstance(route.original_route, APIRoute):
            continue
        calls = dependencies(route.dependant)
        assert route.methods is not None
        for method in route.methods:
            key = (method, route.path)
            if key not in EXPECTED_POLICIES:
                continue
            seen.add(key)
            policy = EXPECTED_POLICIES[key]
            assert policy == "user"
            assert require_user in calls
            assert limit_authenticated in calls

    assert seen == set(EXPECTED_POLICIES), f"missing or extra routes: seen={seen}, expected={set(EXPECTED_POLICIES)}"
