"""
openremap — ECU binary analysis and patching toolkit.

Diff, validate, and apply tuning recipes to automotive ECU binaries.
"""

__version__ = "0.7.5"


def _active_backend() -> str:
    """Return a short string describing which backends are active.

    Always ``"rust"`` — the native extension is mandatory.  Two functions
    (``count_unique_in_window`` and ``find_unique_context``) remain in
    Python because CPython's C-level ``bytes.find`` is slightly faster for
    ECU context-anchor search workloads.
    """
    try:
        from openremap.core.services.entropy import entropy_backend
        return entropy_backend()
    except Exception:
        return "unknown"
