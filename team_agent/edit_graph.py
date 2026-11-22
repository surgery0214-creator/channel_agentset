"""One in-memory LangGraph run per advanced editing transaction."""

from typing import Optional

try:
    from typing_extensions import TypedDict
except ImportError:
    from typing import TypedDict
from uuid import uuid4

from .core import Choose, PlanError


class EditState(TypedDict, total=False):
    ops: list
    text: Optional[str]
    answers: list
    question: dict
    result: dict


class EditRun:
    def __init__(self, apply_plan):
        try:
            from langgraph.checkpoint.memory import InMemorySaver
            from langgraph.graph import END, START, StateGraph
            from langgraph.types import Command, interrupt
        except ImportError as exc:
            raise PlanError("精确编辑需要 Python 3.10+ 和 LangGraph。请运行 python -m pip install -r team_agent/requirements.txt。") from exc

        def validate(state):
            try:
                result = apply_plan(state["ops"], state["answers"], player_text=state["text"], advanced=True)
            except Choose as choice:
                return {"question": {"message": str(choice), "choices": choice.choices, "aliases": choice.aliases}}
            return {"result": result, "question": {}}

        def ask(state):
            answer = interrupt(state["question"])
            return {"answers": [*state["answers"], answer]}

        builder = StateGraph(EditState)
        builder.add_node("validate", validate)
        builder.add_node("ask", ask)
        builder.add_edge(START, "validate")
        builder.add_conditional_edges("validate", lambda s: "ask" if s["question"] else END)
        builder.add_edge("ask", "validate")
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": uuid4().hex}}
        self.command = Command
        self.issue = None

    def start(self, ops, text):
        return self._invoke({"ops": ops, "text": text, "answers": []})

    def resume(self, answer):
        return self._invoke(self.command(resume=answer))

    def _invoke(self, value):
        state = self.graph.invoke(value, self.config)
        if state.get("__interrupt__"):
            question = state["__interrupt__"][0].value
            self.issue = Choose(question["message"], question["choices"])
            self.issue.aliases = question["aliases"]
            return None
        self.issue = None
        return state["result"]
