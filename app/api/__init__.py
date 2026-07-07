"""
App-first REST API package.

This package holds the versioned REST surface introduced by the App-First
Pivot: auth dependencies (`deps.py`), Pydantic request/response schemas
(`schemas.py`), the shared HTTP error mapping (`errors.py`), and the per-domain
routers mounted under ``/api/v1`` by ``register_api()``.

Everything here is a thin HTTP adapter over the existing, unchanged service
layer. No business logic lives in this package.
"""
