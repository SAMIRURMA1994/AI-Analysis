"""LangGraph workflow for the MCP assistant.

The existing Assistant contains the domain-specific routing and safety rules.
This graph provides the explicit request lifecycle around that logic without
duplicating MCP execution code.
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class AssistantState(TypedDict, total=False):
    question: str
    result: dict[str, Any]


def build_assistant_workflow(assistant: Any):
    """Build a request-validation and execution graph for one Assistant."""

    async def validate_request(state: AssistantState) -> AssistantState:
        question = str(state.get("question", "")).strip()
        if not question:
            raise ValueError("message must contain non-whitespace text")
        print("[GRAPH] Request validation node completed", flush=True)
        return {"question": question}

    async def execute_request(state: AssistantState) -> AssistantState:
        print("[GRAPH] Assistant execution node started", flush=True)
        result = await assistant._answer_core(state["question"])
        print("[GRAPH] Assistant execution node completed", flush=True)
        return {"result": result}

    graph = StateGraph(AssistantState)
    graph.add_node("validate_request", validate_request)
    graph.add_node("execute_request", execute_request)
    graph.add_edge(START, "validate_request")
    graph.add_edge("validate_request", "execute_request")
    graph.add_edge("execute_request", END)
    return graph.compile()
