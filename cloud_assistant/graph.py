"""StateGraph assembly and router functions.

The branching lives here, in plain Python reading flat integer state, not in any
prompt. Exactly one LLM influences an edge — the supervisor — and it does so
through a typed ``SupervisorDecision``, which this module reads as a string.

Every router uses ``.get(..., default)``. That is what makes a degraded node
safe: ``_degrade`` zeroes both count fields, so a node that failed routes to END
deterministically instead of raising a KeyError at the routing boundary.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from cloud_assistant.agents.cost_analysis import cost_analysis_node
from cloud_assistant.agents.cost_recommendation import cost_recommendation_node
from cloud_assistant.agents.security_audit import security_audit_node
from cloud_assistant.agents.security_remediation import security_remediation_node
from cloud_assistant.agents.supervisor import supervisor_node
from cloud_assistant.logging_setup import log_decision
from cloud_assistant.state import CloudOpsState


def route_from_supervisor(state: CloudOpsState) -> str:
    """Route to the cost workflow, the security workflow, or straight to END."""
    decision = state.get("workflow") or "unclear"
    log_decision("router", "routing", {"from": "supervisor", "to": decision})
    return decision


def route_after_cost_analysis(state: CloudOpsState) -> str:
    """Continue to recommendations only when there is actual waste to act on."""
    count = state.get("idle_resource_count", 0)
    nxt = "recommend" if count >= 1 else "end"
    log_decision("router", "routing", {"from": "cost_analysis", "to": nxt, "idle_resource_count": count})
    return nxt


def route_after_security_audit(state: CloudOpsState) -> str:
    """Continue to remediation only when the audit actually found something."""
    count = state.get("security_finding_count", 0)
    nxt = "remediate" if count >= 1 else "end"
    log_decision("router", "routing", {"from": "security_audit", "to": nxt, "security_finding_count": count})
    return nxt


def build_graph() -> CompiledStateGraph:
    """Assemble and compile the five-node supervisor graph."""
    g = StateGraph(CloudOpsState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("cost_analysis", cost_analysis_node)
    g.add_node("cost_recommendation", cost_recommendation_node)
    g.add_node("security_audit", security_audit_node)
    g.add_node("security_remediation", security_remediation_node)

    g.add_edge(START, "supervisor")

    # One three-way conditional edge with END in the path map, rather than a
    # separate edge for the unclear case: the supervisor has exactly one decision
    # to make, so it gets exactly one branch point.
    g.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {"cost": "cost_analysis", "security": "security_audit", "unclear": END},
    )

    g.add_conditional_edges(
        "cost_analysis",
        route_after_cost_analysis,
        {"recommend": "cost_recommendation", "end": END},
    )
    g.add_edge("cost_recommendation", END)

    g.add_conditional_edges(
        "security_audit",
        route_after_security_audit,
        {"remediate": "security_remediation", "end": END},
    )
    g.add_edge("security_remediation", END)

    return g.compile()


# Module-level convenience instance. build_graph() stays callable so tests can
# rebuild a clean graph without reusing this one.
graph = build_graph()
