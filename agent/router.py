from agent.deps import CaseyDeps
from agent.models import (
    ModelCommand,
    ModelCommandError,
    handle_model_command,
    parse_model_command,
)
from agent.opencode_client import ModelRef
from agent.operator import is_operator, run_operator
from agent.support_agent import get_client, run_casey


class ModelCommandResult:
    def __init__(self, final_output: str, state: dict):
        self.final_output = final_output
        self._state = state

    def to_input_list(self) -> list:
        return [self._state]


def run_agent(text: str, deps: CaseyDeps, history: list | None):
    state = history[0] if history else {}
    model = model_from(state)

    command = parse_model_command(text)
    if command is not None:
        return _run_model_command(command, state, model)

    if is_operator(deps.user_id):
        return run_operator(text, deps, history, model=model)
    return run_casey(text, deps, history, model=model)


def model_from(state: dict) -> ModelRef | None:
    value = state.get("model")
    return ModelRef.parse(value) if value else None


def _run_model_command(
    command: ModelCommand, state: dict, model: ModelRef | None
) -> ModelCommandResult:
    try:
        reply, chosen, changed = handle_model_command(
            command.argument, get_client(), model
        )
    except ModelCommandError as error:
        return ModelCommandResult(f":no_entry: {error}", state)

    if not changed:
        return ModelCommandResult(reply, state)
    next_state = {**state}
    if chosen is None:
        next_state.pop("model", None)
    else:
        next_state["model"] = str(chosen)
    return ModelCommandResult(reply, next_state)
