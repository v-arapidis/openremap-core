"""
openremap — ECU binary analysis and patching toolkit.

Diff, validate, and apply tuning recipes to automotive ECU binaries.
"""

__version__ = "0.6.2"


def _active_backend() -> str:
    """Return a short string describing which backends are active.

    ``"rust"`` when the native extension is loaded, ``"python"`` when the
    pure-Python fallback is in use.  Set ``OPENREMAP_FORCE_PYTHON=1`` to
    force the pure-Python backend even when the native extension is
    installed (useful for debugging or comparing performance).
    """
    try:
        from openremap.core.services.entropy import entropy_backend
        return entropy_backend()
    except Exception:
        return "unknown"
