from agent.deps import CaseyDeps
from agent.operator import is_operator, run_operator
from agent.support_agent import run_casey


def run_agent(text: str, deps: CaseyDeps, history: list | None):
    if is_operator(deps.user_id):
        return run_operator(text, deps, history)
    return run_casey(text, deps, history)
