"""The local dashboard.

A single-file HTML app served by the standard library. No framework, no CDN, no
build step, no new dependency — because a research tool you cannot start in
three years when the JS ecosystem has moved on is not a durable one.
"""

from .server import serve  # noqa: F401

__all__ = ["serve"]
