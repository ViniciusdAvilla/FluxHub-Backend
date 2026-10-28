"""New integration endpoints are concrete, authenticated route paths."""
from fastapi.routing import APIRoute
from app.main import app


def test_new_paths_are_registered_without_missing_or_duplicate_contract():
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    expected = {
        "/api/v1/orders/{order_id}/financial-details": "GET",
        "/api/v1/exports/{report}": "GET",
        "/api/v1/listing-health/": "GET",
        "/api/v1/agents/claude/status": "GET",
        "/api/v1/agents/claude/run": "POST",
    }
    for path, method in expected.items():
        matching = [route for route in routes
                    if route.path == path and method in route.methods]
        assert len(matching) == 1, (path, method, [(r.path, str(type(r)), getattr(r, "methods", None)) for r in app.routes][-30:])
        assert matching[0].dependencies or matching[0].dependant.dependencies
