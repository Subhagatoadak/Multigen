# orchestrator/services/dsl_parser.py
"""
Full-featured DSL parser for Multigen workflows.
Supports: sequential steps, parallel branches, conditional routing, and dynamic subtree spawns.

Refactored for improved modularity, error handling, and clarity.
"""
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# ==============================================================================
# Error Handling
# ==============================================================================

class DSLParseError(Exception):
    """
    Exception raised for DSL parsing errors.

    Captures the error message and optionally the specific node or part
    of the DSL that caused the error.
    """
    def __init__(self, message: str, node: Optional[Any] = None, step_name: Optional[str] = None):
        """
        Initializes the DSLParseError.

        Args:
            message: The core error message.
            node: The DSL node segment that caused the error (optional).
            step_name: The name of the step being processed when the error occurred (optional).
        """
        full_message = message
        if step_name:
            full_message = f"Error in step '{step_name}': {message}"
        if node is not None:
            # Avoid overly long error messages for large nodes
            node_repr = repr(node)
            if len(node_repr) > 100:
                node_repr = node_repr[:100] + "..."
            full_message = f"{full_message}. Node segment: {node_repr}"

        super().__init__(full_message)
        self.node = node
        self.step_name = step_name

# ==============================================================================
# Data Structures
# ==============================================================================

@dataclass
class ConditionalBranch:
    """Represents a single branch within a conditional step."""
    condition: str  # The condition expression (or 'else')
    step: 'Step'    # The step to execute if the condition is met

@dataclass
class Step:
    """Represents a single parsed step in the workflow."""
    name: str
    agent: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    parallel: List['Step'] = field(default_factory=list)
    conditional: List[ConditionalBranch] = field(default_factory=list)
    dynamic_subtree: Optional[Dict[str, Any]] = None

    def __repr__(self) -> str:
        """Provides a concise representation of the Step."""
        return f"Step(name={self.name!r}, agent={self.agent!r})"

# ==============================================================================
# Core Parsing Logic
# ==============================================================================

def parse_workflow(dsl: Dict[str, Any]) -> List[Step]:
    """
    Parses the top-level workflow DSL dictionary into a list of Step objects.

    Args:
        dsl: The workflow definition dictionary.

    Returns:
        A list of parsed Step objects representing the workflow sequence.

    Raises:
        DSLParseError: If the top-level DSL structure is invalid or empty.
    """
    if not isinstance(dsl, dict):
        raise DSLParseError("Workflow DSL must be a dictionary", node=dsl)

    steps_data = dsl.get('steps')
    if not isinstance(steps_data, list):
        # Provide context about where 'steps' was expected
        raise DSLParseError("Workflow DSL must contain a 'steps' key with a list of step definitions", node=dsl)

    if not steps_data:
        raise DSLParseError("The 'steps' list in the workflow DSL cannot be empty")

    parsed_steps: List[Step] = []
    for i, step_node in enumerate(steps_data):
        try:
            parsed_steps.append(parse_step(step_node))
        except DSLParseError as e:
            # Add context if the error didn't already capture the step name
            if e.step_name is None and isinstance(step_node, dict) and 'name' in step_node:
                 e.step_name = step_node.get('name', f'step_{i+1}') # Fallback name
            raise # Re-raise the enriched error
        except Exception as e:
            # Catch unexpected errors during parsing of a specific step
            step_name = step_node.get('name', f'step_{i+1}') if isinstance(step_node, dict) else f'step_{i+1}'
            raise DSLParseError(f"Unexpected error parsing step: {e}", node=step_node, step_name=step_name) from e

    return parsed_steps

def parse_step(node: Dict[str, Any]) -> Step:
    """
    Parses a single step definition dictionary into a Step object.

    Handles nested structures like parallel branches and conditional logic,
    performing validation at each stage.

    Args:
        node: The dictionary representing a single step definition.

    Returns:
        A parsed Step object.

    Raises:
        DSLParseError: If the step definition is invalid.
    """
    if not isinstance(node, dict):
        raise DSLParseError("Each step definition must be a dictionary", node=node)

    step_name = node.get('name')
    if not step_name or not isinstance(step_name, str):
        raise DSLParseError("Step definition must have a valid 'name' field (string)", node=node)

    # Create the base step
    step = Step(name=step_name)

    # Parse individual components using helper functions
    _parse_agent_and_params(node, step)
    step.parallel = _parse_parallel_steps(node, step_name)
    step.conditional = _parse_conditional_branches(node, step_name)
    step.dynamic_subtree = _parse_dynamic_subtree(node, step_name)

    # Validation: Ensure the step performs some action
    _validate_step_has_action(step, node)

    # Validation: Warn about unrecognized keys
    _warn_unknown_keys(node, step_name)

    return step

# ==============================================================================
# Private Helper Functions for Parsing Step Components
# ==============================================================================

def _parse_agent_and_params(node: Dict[str, Any], step: Step) -> None:
    """Parses 'agent' and 'params' from the node and updates the Step object."""
    step_name = step.name # Already validated in parse_step

    agent = node.get('agent')
    if agent is not None:
        if not isinstance(agent, str):
            raise DSLParseError("'agent' must be a string", node=node, step_name=step_name)
        step.agent = agent

    params = node.get('params', {}) # Default to empty dict if not present
    if not isinstance(params, dict):
        raise DSLParseError("'params' must be a dictionary", node=node, step_name=step_name)
    step.params = params

def _parse_parallel_steps(node: Dict[str, Any], step_name: str) -> List[Step]:
    """Parses the 'parallel' list from the node."""
    parallel_nodes = node.get('parallel')
    if parallel_nodes is None:
        return [] # No parallel steps defined

    if not isinstance(parallel_nodes, list):
        raise DSLParseError("'parallel' must be a list of step definitions", node=node, step_name=step_name)

    parsed_parallel: List[Step] = []
    for i, p_node in enumerate(parallel_nodes):
        try:
            # Recursively parse each parallel step
            parsed_parallel.append(parse_step(p_node))
        except DSLParseError as e:
             # Add context if the error didn't already capture the inner step name
            if e.step_name is None and isinstance(p_node, dict) and 'name' in p_node:
                 e.step_name = p_node.get('name', f'{step_name}_parallel_{i+1}')
            raise DSLParseError(f"Error parsing step within 'parallel' list (index {i})", node=p_node, step_name=step_name) from e
        except Exception as e:
            p_step_name = p_node.get('name', f'{step_name}_parallel_{i+1}') if isinstance(p_node, dict) else f'{step_name}_parallel_{i+1}'
            raise DSLParseError(f"Unexpected error parsing parallel step: {e}", node=p_node, step_name=p_step_name) from e

    return parsed_parallel

def _parse_conditional_branches(node: Dict[str, Any], step_name: str) -> List[ConditionalBranch]:
    """Parses 'conditional' list and 'else' block from the node."""
    conditional_branches: List[ConditionalBranch] = []

    conditional_nodes = node.get('conditional')
    if conditional_nodes is not None:
        if not isinstance(conditional_nodes, list):
            raise DSLParseError("'conditional' must be a list of conditional branch definitions", node=node, step_name=step_name)

        for i, cond_node in enumerate(conditional_nodes):
            if not isinstance(cond_node, dict):
                raise DSLParseError(f"Each entry in 'conditional' list (index {i}) must be a dictionary", node=cond_node, step_name=step_name)

            condition_expr = cond_node.get('condition')
            if not condition_expr or not isinstance(condition_expr, str):
                raise DSLParseError(f"Invalid or missing 'condition' (string) in conditional branch (index {i})", node=cond_node, step_name=step_name)
            if condition_expr.lower() == 'else':
                 raise DSLParseError(f"'condition: else' is not allowed within the 'conditional' list; use the top-level 'else' key instead.", node=cond_node, step_name=step_name)

            then_node_spec = cond_node.get('then')
            if then_node_spec is None:
                raise DSLParseError(f"Missing 'then' branch in conditional branch (index {i})", node=cond_node, step_name=step_name)

            try:
                # Normalize and parse the 'then' branch step
                normalized_then_node = _normalize_branch_node(then_node_spec, step_name, f"then_{i}")
                then_step = parse_step(normalized_then_node)
                conditional_branches.append(ConditionalBranch(condition=condition_expr, step=then_step))
            except DSLParseError as e:
                 raise DSLParseError(f"Error parsing 'then' branch for condition '{condition_expr}' (index {i})", node=then_node_spec, step_name=step_name) from e
            except Exception as e:
                 raise DSLParseError(f"Unexpected error parsing 'then' branch for condition '{condition_expr}': {e}", node=then_node_spec, step_name=step_name) from e


    # Handle the optional 'else' block
    else_node_spec = node.get('else')
    if else_node_spec is not None:
        try:
            # Normalize and parse the 'else' branch step
            normalized_else_node = _normalize_branch_node(else_node_spec, step_name, "else")
            else_step = parse_step(normalized_else_node)
            conditional_branches.append(ConditionalBranch(condition='else', step=else_step))
        except DSLParseError as e:
             raise DSLParseError(f"Error parsing 'else' branch", node=else_node_spec, step_name=step_name) from e
        except Exception as e:
             raise DSLParseError(f"Unexpected error parsing 'else' branch: {e}", node=else_node_spec, step_name=step_name) from e


    return conditional_branches

def _parse_dynamic_subtree(node: Dict[str, Any], step_name: str) -> Optional[Dict[str, Any]]:
    """Parses the 'dynamic_subtree' definition from the node."""
    dyn_subtree_node = node.get('dynamic_subtree')
    if dyn_subtree_node is None:
        return None # No dynamic subtree defined

    if not isinstance(dyn_subtree_node, dict):
        raise DSLParseError("'dynamic_subtree' must be a dictionary", node=node, step_name=step_name)

    # Future validation could go here (e.g., checking required keys within dynamic_subtree)
    return dyn_subtree_node

def _normalize_branch_node(branch_spec: Union[Dict[str, Any], str], parent_name: str, branch_type: str) -> Dict[str, Any]:
    """
    Normalizes a branch specification (from 'then' or 'else') into a
    standard step dictionary format suitable for parsing.

    Allows shorthand where the branch is just an agent name string.

    Args:
        branch_spec: The branch definition (string or dictionary).
        parent_name: The name of the step containing this branch.
        branch_type: Identifier for the branch ('then_0', 'else', etc.).

    Returns:
        A dictionary representing the normalized step definition for the branch.

    Raises:
        DSLParseError: If the branch specification is invalid.
    """
    err_context = f"in '{branch_type}' branch of step '{parent_name}'"

    # Shorthand: Branch is just an agent name string
    if isinstance(branch_spec, str):
        if not branch_spec:
             raise DSLParseError(f"Branch agent shorthand cannot be an empty string {err_context}", node=branch_spec)
        # Default branch step name convention
        return {'name': f"{parent_name}_{branch_type}_branch", 'agent': branch_spec}

    # Standard: Branch is a dictionary (potentially a full step definition)
    if isinstance(branch_spec, dict):
        # If it's a full step definition, use it directly but ensure 'name' exists
        if 'name' in branch_spec:
             if not isinstance(branch_spec['name'], str) or not branch_spec['name']:
                 raise DSLParseError(f"Branch dictionary has invalid 'name' {err_context}", node=branch_spec)
             # Ensure it has an action or nested structure later in parse_step
             return branch_spec
        # If it's a partial definition needing normalization (like the old expand_branch)
        elif 'agent' in branch_spec:
             agent = branch_spec.get('agent')
             if not agent or not isinstance(agent, str):
                raise DSLParseError(f"Branch dictionary missing valid 'agent' (string) {err_context}", node=branch_spec)

             # Construct a normalized node
             normalized_node: Dict[str, Any] = {
                'name': f"{parent_name}_{branch_type}_branch", # Default name if not provided
                'agent': agent
             }
             # Copy optional fields if present and valid
             if 'params' in branch_spec:
                if not isinstance(branch_spec['params'], dict):
                     raise DSLParseError(f"'params' must be a dict {err_context}", node=branch_spec)
                normalized_node['params'] = branch_spec['params']
             # Note: Allowing nested parallel/conditional within branches directly
             # might be complex. Current design parses them *after* normalization.
             # If required, add similar checks/copying for 'parallel', 'conditional' etc. here.

             return normalized_node
        else:
             raise DSLParseError(f"Branch dictionary must contain at least 'name' or 'agent' {err_context}", node=branch_spec)

    # Invalid type
    raise DSLParseError(f"Invalid branch specification (must be string or dictionary) {err_context}", node=branch_spec)


# ==============================================================================
# Validation and Utility Functions
# ==============================================================================

def _validate_step_has_action(step: Step, original_node: Dict[str, Any]) -> None:
    """Ensures a Step object defines at least one execution path."""
    has_action = (
        step.agent is not None or
        bool(step.parallel) or
        bool(step.conditional) or
        step.dynamic_subtree is not None
    )
    if not has_action:
        raise DSLParseError(
            "Step must define at least one of: 'agent', 'parallel', 'conditional', or 'dynamic_subtree'",
            node=original_node,
            step_name=step.name
        )

def _warn_unknown_keys(node: Dict[str, Any], step_name: str) -> None:
    """Issues a warning if unrecognized keys are found in a step definition."""
    known_keys = {
        'name', 'agent', 'params', 'parallel', 'conditional', 'else', 'dynamic_subtree'
    }
    unknown_keys = set(node.keys()) - known_keys
    if unknown_keys:
        warnings.warn(
            f"Step '{step_name}': Unknown keys {unknown_keys} found and will be ignored.",
            SyntaxWarning,
            stacklevel=3 # Point warning to the caller of parse_step
        )
