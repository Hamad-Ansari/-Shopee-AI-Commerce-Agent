"""Typed tool registry.

NON-NEGOTIABLE: the agent may only call READ and PREPARE tools. EXECUTE tools
(place order, publish listing) may only be invoked by the executor path after a
valid approval — the registry enforces this by requiring `approved_by`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from backend.dependencies import get_product_service
from backend.exceptions import PermissionDeniedError, ToolNotFoundError
from backend.services import order_service, seller_service


class PermissionLevel(str, Enum):
    READ = "READ"
    PREPARE = "PREPARE"
    EXECUTE = "EXECUTE"


@dataclass
class Tool:
    name: str
    level: PermissionLevel
    description: str
    fn: Callable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def call(self, name: str, db, *, approved_by: str | None = None, **kwargs):
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(f"Unknown tool '{name}'.")
        if tool.level == PermissionLevel.EXECUTE and not approved_by:
            raise PermissionDeniedError(
                f"EXECUTE tool '{name}' may only be called by the executor node after a "
                "valid approval."
            )
        return tool.fn(db, **kwargs)


def build_tool_registry() -> ToolRegistry:
    reg = ToolRegistry()
    ps = get_product_service()

    # READ tools — permitted without approval.
    reg.register(Tool("search_products", PermissionLevel.READ, "Search the product catalog",
                      lambda db, **kw: ps.search(**kw)))
    reg.register(Tool("get_product", PermissionLevel.READ, "Get one product by id",
                      lambda db, **kw: ps.get(kw["product_id"])))
    reg.register(Tool("get_order_status", PermissionLevel.READ, "Latest order status for a user",
                      lambda db, **kw: order_service.get_order_status(db, **kw)))

    # PREPARE tools — build proposals/drafts, never execute.
    reg.register(Tool("prepare_order_proposal", PermissionLevel.PREPARE, "Create an order proposal (no execution)",
                      lambda db, **kw: order_service.prepare_order_proposal(db, **kw)))
    reg.register(Tool("create_listing_draft", PermissionLevel.PREPARE, "Validate + save a listing draft",
                      lambda db, **kw: seller_service.create_draft(db, **kw)))
    reg.register(Tool("reject_order_proposal", PermissionLevel.PREPARE, "Reject a pending order proposal",
                      lambda db, **kw: order_service.reject(db, **kw)))
    reg.register(Tool("reject_listing_proposal", PermissionLevel.PREPARE, "Reject a pending listing publication",
                      lambda db, **kw: seller_service.reject_publish(db, **kw)))

    # EXECUTE tools — executor node only, after a valid approval.
    reg.register(Tool("execute_place_order", PermissionLevel.EXECUTE, "Approve + revalidate + place an order",
                      lambda db, **kw: order_service.approve_and_execute(db, **kw)))
    reg.register(Tool("execute_publish_listing", PermissionLevel.EXECUTE, "Approve + publish a listing",
                      lambda db, **kw: seller_service.approve_and_publish(db, **kw)))
    return reg
