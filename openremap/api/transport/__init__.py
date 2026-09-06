"""
openremap.api.transport — thin adapters over the registry dispatcher.

No business logic lives here; each transport parses requests, calls
``registry.dispatch``, and formats responses.
"""
