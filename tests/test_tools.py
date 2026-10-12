import pytest

from backend.exceptions import PermissionDeniedError, ToolNotFoundError


def test_execute_tools_require_the_executor(db, orch):
    """Rule 2: the agent cannot call EXECUTE tools — only the executor after approval."""
    with pytest.raises(PermissionDeniedError):
        orch.tools.call("execute_place_order", db, proposal_id="x", user_id="u")
    with pytest.raises(PermissionDeniedError):
        orch.tools.call("execute_publish_listing", db, proposal_id="x", user_id="u")


def test_unknown_tool_raises(db, orch):
    with pytest.raises(ToolNotFoundError):
        orch.tools.call("nope", db)


def test_read_and_prepare_tools_do_not_require_approval(db, orch):
    results = orch.tools.call("search_products", db, query="earbuds", limit=3)
    assert results
