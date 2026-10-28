"""Regression contract for the five newly exposed endpoints."""
from fastapi.routing import APIRoute

from app.main import app
from app.routers.orders import router as orders_router
from app.routers.exports import router as exports_router
from app.routers.listing_health import router as health_router
from app.routers.claude_agents import router as claude_router


def test_new_routes_are_exposed_once_and_require_user_auth():
    expected = [
        (orders_router, "/{order_id}/financial-details", "GET",
         "/api/v1/orders/{order_id}/financial-details"),
        (exports_router, "/{report}", "GET", "/api/v1/exports/{report}"),
        (health_router, "/", "GET", "/api/v1/listing-health/"),
        (claude_router, "/status", "GET", "/api/v1/agents/claude/status"),
        (claude_router, "/run", "POST", "/api/v1/agents/claude/run"),
    ]
    # FastAPI versions can keep _IncludedRouter placeholders until the OpenAPI
    # schema is assembled; checking only app.routes misses those registered routes.
    paths = app.openapi()["paths"]
    for router, relative_path, method, full_path in expected:
        matches = [route for route in router.routes
                   if isinstance(route, APIRoute) and
                   route.path == router.prefix + relative_path and
                   method in route.methods]
        assert len(matches) == 1, (router.prefix, relative_path, method)
        assert matches[0].dependant.dependencies
        assert full_path in paths and method.lower() in paths[full_path]
