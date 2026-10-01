"""List every route the app serves, whichever FastAPI version is installed.

FastAPI used to copy the routes of an included router straight into
``app.routes``. Newer releases keep each included router as a single entry there
and resolve its routes (prefix and dependencies already applied) on demand, so
walking ``app.routes`` and filtering for ``APIRoute`` finds nothing under
``/api/v1``. Tests that audit the route table go through this module instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute


def flattened_routes(application: FastAPI) -> list[Any]:
    """Return every API route with its full path, methods and dependencies.

    The entries are ``APIRoute`` objects on older FastAPI and the equivalent
    resolved route contexts on newer FastAPI; both expose ``path``, ``methods``,
    ``endpoint``, ``response_model`` and ``dependant``.

    Args:
        application: The FastAPI application whose route table to read.

    Returns:
        The flattened routes, in registration order.
    """
    routes: list[Any] = []
    for entry in application.routes:
        if hasattr(entry, "effective_route_contexts"):
            routes.extend(entry.effective_route_contexts())
        elif isinstance(entry, APIRoute):
            routes.append(entry)
    return routes
