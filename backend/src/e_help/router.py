from __future__ import annotations

from typing import Literal

from .models import QueryContext

RouteName = Literal["blocked", "chitchat", "rag", "tool"]


def route_query(context: QueryContext) -> RouteName:
    if context["safety_status"] == "block":
        return "blocked"
    if context["intent"] == "chitchat":
        return "chitchat"
    if context["intent"] == "tool":
        return "tool"
    return "rag"
