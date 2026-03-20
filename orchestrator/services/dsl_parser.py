"""
Full-featured DSL parser for Multigen workflows.
Supports sequential steps, parallel branches, conditional routing, and dynamic subtree spawns.

This module provides robust parsing, validation, and structured error reporting.
"""
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


class DSLParseError(Exception):
    """
    Exception raised for errors encountered while parsing the DSL.

    Attributes:
        node: The part of the DSL that caused the error.
        step_name: The name of the step being processed when the error occurred.
    """

    def __init__(
        self,
        message: str,
        node: Optional[Any] = None,
        step_name: Optional[str] = None,
    ) -> None:
        full_message = message
        if step_name:
            full_message = f"Error in step '{step_name}': {message}"
        if node is not None:
            node_repr = repr(node)
            if len(node_repr) > 100:
                node_repr = node_repr[:100] + "..."
            full_message = f"{full_message}. Node segment: {node_repr}"
        super().__init__(full_message)
        self.node = node
        self.step_name = step_name


@dataclass
class ConditionalBranch:
    """
    Represents a single branch within a conditional step.

    Attributes:
        condition: The condition expression (or 'else').
        step: The Step to execute if the condition is met.
    """
    condition: str
    step: 'Step'


@dataclass
class Step:
    """
    Represents a single parsed step in the workflow.

    Attributes:
        name: The unique name of the step.
        agent: Optional agent identifier to invoke.
        params: Dictionary of parameters for the agent.
        parallel: Sub-steps to execute in parallel.
        conditional: Conditional branches off this step.
        dynamic_subtree: Definition for spawning a dynamic subtree.
        loop: Loop configuration for cyclic execution.
    """
    name: str
    agent: Optional[str] = None
    agent_version: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    parallel: List['Step'] = field(default_factory=list)
    conditional: List[ConditionalBranch] = field(default_factory=list)
    dynamic_subtree: Optional[Dict[str, Any]] = None
    loop: Optional[Dict[str, Any]] = None
    graph: Optional[Dict[str, Any]] = None

    def __repr__(self) -> str:
        return f"Step(name={self.name!r}, agent={self.agent!r})"


def parse_workflow(dsl: Dict[str, Any]) -> List[Step]:
    """
    Parse the top-level workflow DSL into a list of Step objects.

    Args:
        dsl: A dictionary representing the workflow definition.

    Returns:
        A list of parsed Step objects.

    Raises:
        DSLParseError: If the structure is invalid or empty.
    """
    if not isinstance(dsl, dict):
        raise DSLParseError("Workflow DSL must be a dictionary", node=dsl)

    steps_data = dsl.get('steps')
    if not isinstance(steps_data, list):
        raise DSLParseError(
            "Workflow DSL must contain a 'steps' key with a list of step definitions",
            node=dsl,
        )
    if not steps_data:
        raise DSLParseError("The 'steps' list in the workflow DSL cannot be empty")

    parsed_steps: List[Step] = []
    for idx, step_node in enumerate(steps_data):
        try:
            parsed_steps.append(parse_step(step_node))
        except DSLParseError as exc:
            if exc.step_name is None and isinstance(step_node, dict) and 'name' in step_node:
                exc.step_name = step_node['name']
            raise
        except Exception as exc:
            name = step_node.get('name') if isinstance(step_node, dict) and 'name' in step_node else f'step_{idx+1}'
            raise DSLParseError(
                f"Unexpected error parsing step: {exc}",
                node=step_node,
                step_name=name,
            ) from exc

    return parsed_steps


def parse_step(node: Dict[str, Any]) -> Step:
    """
    Parse a single step definition dictionary into a Step object.

    Args:
        node: The dictionary representing one step.

    Returns:
        A fully constructed Step.

    Raises:
        DSLParseError: If validation fails.
    """
    if not isinstance(node, dict):
        raise DSLParseError("Each step definition must be a dictionary", node=node)

    name = node.get('name')
    if not name or not isinstance(name, str):
        raise DSLParseError("Step definition must have a valid 'name' field (string)", node=node)

    step = Step(name=name)
    _parse_agent_and_params(node, step)
    step.parallel = _parse_parallel_steps(node, name)
    step.conditional = _parse_conditional_branches(node, name)
    step.dynamic_subtree = _parse_dynamic_subtree(node, name)
    step.loop = _parse_loop(node, name)
    step.graph = _parse_graph(node, name)

    _validate_step_has_action(step, node)
    _warn_unknown_keys(node, name)
    return step


def _parse_agent_and_params(node: Dict[str, Any], step: Step) -> None:
    """
    Extract 'agent' and 'params' keys and apply to the Step.

    Raises DSLParseError if types are invalid.
    """
    agent = node.get('agent')
    if agent is not None:
        if not isinstance(agent, str):
            raise DSLParseError("'agent' must be a string", node=node, step_name=step.name)
        step.agent = agent

    agent_version = node.get('agent_version')
    if agent_version is not None:
        if not isinstance(agent_version, str):
            raise DSLParseError("'agent_version' must be a string", node=node, step_name=step.name)
        step.agent_version = agent_version

    params = node.get('params', {})
    if not isinstance(params, dict):
        raise DSLParseError("'params' must be a dictionary", node=node, step_name=step.name)
    step.params = params


def _parse_parallel_steps(node: Dict[str, Any], step_name: str) -> List[Step]:
    """
    Parse the 'parallel' key into sub-steps to execute concurrently.
    """
    pnodes = node.get('parallel')
    if pnodes is None:
        return []
    if not isinstance(pnodes, list):
        raise DSLParseError("'parallel' must be a list of step definitions", node=node, step_name=step_name)

    result: List[Step] = []
    for idx, subnode in enumerate(pnodes):
        try:
            result.append(parse_step(subnode))
        except DSLParseError as exc:
            if exc.step_name is None and isinstance(subnode, dict) and 'name' in subnode:
                exc.step_name = subnode['name']
            raise DSLParseError(
                f"Error parsing step within 'parallel' list (index {idx})",
                node=subnode,
                step_name=step_name,
            ) from exc
    return result


def _parse_conditional_branches(node: Dict[str, Any], step_name: str) -> List[ConditionalBranch]:
    """
    Parse 'conditional' list plus optional 'else' into branches.
    """
    branches: List[ConditionalBranch] = []
    cnodes = node.get('conditional')
    if cnodes is not None:
        if not isinstance(cnodes, list):
            raise DSLParseError(
                "'conditional' must be a list of conditional branch definitions",
                node=node,
                step_name=step_name,
            )
        for idx, entry in enumerate(cnodes):
            if not isinstance(entry, dict):
                raise DSLParseError(
                    f"Each entry in 'conditional' list (index {idx}) must be a dictionary",
                    node=entry,
                    step_name=step_name,
                )
            cond = entry.get('condition')
            if not cond or not isinstance(cond, str):
                raise DSLParseError(
                    f"Invalid or missing 'condition' (string) in conditional branch (index {idx})",
                    node=entry,
                    step_name=step_name,
                )
            if cond.lower() == 'else':
                raise DSLParseError(
                    "'condition: else' is not allowed within the 'conditional' list; use the top-level 'else' key instead.",
                    node=entry,
                    step_name=step_name,
                )
            then_spec = entry.get('then')
            if then_spec is None:
                raise DSLParseError(
                    f"Missing 'then' branch in conditional branch (index {idx})",
                    node=entry,
                    step_name=step_name,
                )
            norm = _normalize_branch_node(then_spec, step_name, f"then_{idx}")
            branches.append(ConditionalBranch(condition=cond, step=parse_step(norm)))

    es = node.get('else')
    if es is not None:
        norm = _normalize_branch_node(es, step_name, 'else')
        branches.append(ConditionalBranch(condition='else', step=parse_step(norm)))

    return branches


def _parse_graph(node: Dict[str, Any], step_name: str) -> Optional[Dict[str, Any]]:
    """
    Parse a graph step definition.

    Expected shape:
        graph:
          entry: "node_id"
          max_cycles: 5
          nodes:
            - {id, agent, params, tools, retry, timeout}
          edges:
            - {source, target, condition}
    """
    graph = node.get("graph")
    if graph is None:
        return None
    if not isinstance(graph, dict):
        raise DSLParseError("'graph' must be a dictionary", node=node, step_name=step_name)
    if "entry" not in graph:
        raise DSLParseError("'graph' must have an 'entry' node id", node=node, step_name=step_name)
    if not isinstance(graph.get("nodes"), list) or not graph["nodes"]:
        raise DSLParseError("'graph.nodes' must be a non-empty list", node=node, step_name=step_name)
    return graph


def _parse_loop(node: Dict[str, Any], step_name: str) -> Optional[Dict[str, Any]]:
    """
    Parse a loop step definition.

    Expected shape:
        loop:
          until: "quality_score >= 0.9"   # condition checked after each iteration
          max_iterations: 5               # hard cap (default 10)
          steps: [...]                    # sub-steps to execute each iteration
    """
    loop = node.get('loop')
    if loop is None:
        return None
    if not isinstance(loop, dict):
        raise DSLParseError("'loop' must be a dictionary", node=node, step_name=step_name)

    loop_steps = loop.get('steps')
    if not isinstance(loop_steps, list) or not loop_steps:
        raise DSLParseError(
            "'loop.steps' must be a non-empty list of step definitions",
            node=node,
            step_name=step_name,
        )

    for idx, sub in enumerate(loop_steps):
        try:
            parse_step(sub)
        except DSLParseError as exc:
            raise DSLParseError(
                f"Error in loop sub-step at index {idx}: {exc}",
                node=sub,
                step_name=step_name,
            ) from exc

    return loop


def _parse_dynamic_subtree(node: Dict[str, Any], step_name: str) -> Optional[Dict[str, Any]]:
    """
    Parse a dynamic subtree definition.
    Returns the subtree dict or None if absent.
    """
    ds = node.get('dynamic_subtree')
    if ds is None:
        return None
    if not isinstance(ds, dict):
        raise DSLParseError("'dynamic_subtree' must be a dictionary", node=node, step_name=step_name)
    return ds


def _normalize_branch_node(
    branch_spec: Union[Dict[str, Any], str],
    parent_name: str,
    branch_type: str,
) -> Dict[str, Any]:
    """
    Normalize a 'then' or 'else' branch spec to a full step dict.
    """
    ctx = f"in '{branch_type}' branch of step '{parent_name}'"
    if isinstance(branch_spec, str):
        if not branch_spec:
            raise DSLParseError(f"Branch agent shorthand cannot be an empty string {ctx}", node=branch_spec)
        return {'name': f"{parent_name}_{branch_type}_branch", 'agent': branch_spec}
    if not isinstance(branch_spec, dict):
        raise DSLParseError(f"Invalid branch specification (must be string or dictionary) {ctx}", node=branch_spec)
    if 'name' in branch_spec:
        nm = branch_spec['name']
        if not isinstance(nm, str) or not nm:
            raise DSLParseError(f"Branch dictionary has invalid 'name' {ctx}", node=branch_spec)
        return branch_spec
    if 'agent' in branch_spec:
        av = branch_spec['agent']
        if not isinstance(av, str) or not av:
            raise DSLParseError(f"Branch dictionary missing valid 'agent' (string) {ctx}", node=branch_spec)
        norm: Dict[str, Any] = {'name': f"{parent_name}_{branch_type}_branch", 'agent': av}
        pv = branch_spec.get('params')
        if pv is not None:
            if not isinstance(pv, dict):
                raise DSLParseError(f"'params' must be a dict {ctx}", node=branch_spec)
            norm['params'] = pv
        return norm
    raise DSLParseError(f"Branch dictionary must contain at least 'name' or 'agent' {ctx}", node=branch_spec)


def _validate_step_has_action(step: Step, node: Dict[str, Any]) -> None:
    """
    Ensure the step has at least one action: agent, parallel, conditional, dynamic_subtree, or loop.
    """
    if not (step.agent or step.parallel or step.conditional or step.dynamic_subtree or step.loop or step.graph):
        raise DSLParseError(
            "Step must define at least one of: 'agent', 'parallel', 'conditional', 'dynamic_subtree', or 'loop'",
            node=node,
            step_name=step.name,
        )


def _warn_unknown_keys(node: Dict[str, Any], step_name: str) -> None:
    """
    Log a warning if there are unrecognized keys in the step definition.
    """
    known = {'name', 'agent', 'agent_version', 'params', 'parallel', 'conditional', 'else', 'dynamic_subtree', 'loop', 'graph'}
    extra = [k for k in node.keys() if k not in known]
    if extra:
        literal = '{' + ', '.join(f"'{k}'" for k in extra) + '}'
        warnings.warn(
            f"Step '{step_name}': Unknown keys {literal} found and will be ignored.",
            SyntaxWarning,
            stacklevel=3,
        )


if __name__ == "__main__":
    # Simple evaluator to confirm all expected functions are present
    import inspect

    expected = [
        'parse_workflow',
        'parse_step',
        '_parse_agent_and_params',
        '_parse_parallel_steps',
        '_parse_conditional_branches',
        '_parse_dynamic_subtree',
        '_normalize_branch_node',
        '_validate_step_has_action',
        '_warn_unknown_keys',
        'DSLParseError',
        'Step',
        'ConditionalBranch',
    ]
    missing = [name for name in expected if name not in globals()]
    if missing:
        print(f"⚠️  Missing definitions: {missing}")
    else:
        print("✅ All expected parser components are present.")
