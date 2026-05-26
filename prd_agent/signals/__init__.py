from .types import UnifiedSignal

__all__ = ["SignalRouter", "UnifiedSignal"]


def __getattr__(name: str):
    if name == "SignalRouter":
        from .router import SignalRouter

        return SignalRouter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
