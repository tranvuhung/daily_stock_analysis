# -*- coding: utf-8 -*-
"""
===================================
API v1 module initialisation
===================================

Responsibilities:
1. Lazily export the v1 API router so a schema import does not pull in the whole endpoint import tree
"""

from typing import Any

__all__ = ["api_v1_router"]


def __getattr__(name: str) -> Any:
    if name != "api_v1_router":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from api.v1.router import router

    return router
