"""Python client for the Edge Grid gateway.

Imports are lazy so that `python -m sdk.edgegrid_sdk` does not import the module
twice (once as a package attribute, once as `__main__`), which produces a
RuntimeWarning and can leave two copies of the module's state in play.
"""

__all__ = ["EdgeGrid", "Completion", "EdgeGridError"]


def __getattr__(name: str):
    if name in __all__:
        from sdk import edgegrid_sdk

        return getattr(edgegrid_sdk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
