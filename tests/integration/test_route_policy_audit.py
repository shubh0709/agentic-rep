"""Explicit mounted-route inventory: new routes require a security decision."""

import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from sra.api.rate_limit import limit_anonymous, limit_authenticated
from sra.auth.dependencies import require_admin, require_user

pytestmark = pytest.mark.phase3


def test_every_mounted_route_has_an_explicit_authentication_and_limit_policy(client):
    policies = {
        ("GET", "/api/health"): "public",
        ("GET", "/api/v1/auth/google/login"): "anonymous",
        ("GET", "/api/v1/auth/google/callback"): "anonymous",
        ("POST", "/api/v1/auth/desktop/exchange"): "anonymous",
        ("POST", "/api/v1/auth/logout"): "anonymous",
        ("GET", "/api/v1/auth/me"): "user",
        ("POST", "/api/v1/entries/ingest"): "user",
        ("GET", "/api/v1/search"): "user",
        ("GET", "/api/v1/digest"): "user",
        ("GET", "/api/v1/digest/ready"): "user",
        ("POST", "/api/v1/chat"): "user",
        ("GET", "/api/v1/quiz/today"): "user",
        ("GET", "/api/v1/quiz/{quiz_set_id}"): "user",
        ("POST", "/api/v1/quiz/{quiz_set_id}/attempts"): "user",
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/answers"): "user",
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/voice-turn"): "user",
        ("POST", "/api/v1/quiz/attempts/{attempt_id}/complete"): "user",
        ("GET", "/api/v1/quiz/attempts/{attempt_id}"): "user",
        ("GET", "/api/v1/me/preferences"): "user",
        ("PUT", "/api/v1/me/preferences"): "user",
        ("GET", "/api/v1/admin/usage"): "admin",
        ("GET", "/api/v1/admin/rate-limits"): "admin",
    }
    framework_public = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    seen = set()

    def dependencies(dependant):
        calls = {dependant.call}
        for child in dependant.dependencies:
            calls.update(dependencies(child))
        return calls

    for route in iter_route_contexts(client.app.routes):
        assert route.path is not None
        if not isinstance(route.original_route, APIRoute):
            assert route.path in framework_public
            framework_public.remove(route.path)
            continue
        calls = dependencies(route.dependant)
        assert route.methods is not None
        for method in route.methods:
            key = (method, route.path)
            assert key in policies, f"Unaudited route: {key}"
            seen.add(key)
            policy = policies[key]
            if policy in {"user", "admin"}:
                assert {require_user, limit_authenticated} <= calls
            elif policy == "anonymous":
                assert limit_anonymous in calls
                assert not {require_user, require_admin, limit_authenticated} & calls
            else:
                assert not {require_user, require_admin, limit_authenticated, limit_anonymous} & calls
            if policy == "admin":
                assert require_admin in calls
    assert seen == policies.keys()
    assert not framework_public