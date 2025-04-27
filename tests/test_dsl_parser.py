import pytest
import warnings # Import warnings module for testing warnings

# Assuming dsl_parser is accessible (adjust path if necessary)
from orchestrator.services.dsl_parser import (
    parse_workflow,
    parse_step, # Keep import if you want finer-grained step tests, but focus on parse_workflow
    Step,
    ConditionalBranch,
    DSLParseError
)

# ==================================
# Happy Path Tests
# ==================================

def test_simple_step():
    """Tests parsing a basic step with name and agent."""
    dsl = {"steps": [{"name": "s1", "agent": "AgentA", "params": {"p1": "v1"}}]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    assert isinstance(steps[0], Step)
    assert steps[0].name == "s1"
    assert steps[0].agent == "AgentA"
    assert steps[0].params == {"p1": "v1"}
    assert not steps[0].parallel
    assert not steps[0].conditional
    assert steps[0].dynamic_subtree is None

def test_sequential_steps():
    """Tests parsing multiple steps in sequence."""
    dsl = {"steps": [
        {"name": "s1", "agent": "AgentA"},
        {"name": "s2", "agent": "AgentB"}
    ]}
    steps = parse_workflow(dsl)
    assert len(steps) == 2
    assert steps[0].name == "s1" and steps[0].agent == "AgentA"
    assert steps[1].name == "s2" and steps[1].agent == "AgentB"

def test_parallel_branch():
    """Tests parsing a step with parallel branches."""
    dsl = {"steps": [{
        "name": "parallel_parent",
        "parallel": [
            {"name": "p1", "agent": "AgentA", "params": {"a": 1}},
            {"name": "p2", "agent": "AgentB"}
        ]
    }]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    parent_step = steps[0]
    assert parent_step.name == "parallel_parent"
    assert parent_step.agent is None # Parent doesn't have its own agent here
    assert len(parent_step.parallel) == 2
    assert isinstance(parent_step.parallel[0], Step)
    assert parent_step.parallel[0].name == "p1"
    assert parent_step.parallel[0].agent == "AgentA"
    assert parent_step.parallel[0].params == {"a": 1}
    assert parent_step.parallel[1].name == "p2"
    assert parent_step.parallel[1].agent == "AgentB"

def test_conditional_branches_with_normalization():
    """Tests parsing conditionals, including shorthand and else, checking normalization."""
    dsl = {"steps": [{
        "name": "conditional_parent",
        "conditional": [
            {"condition": "context.status == 'ok'", "then": "AgentOK"}, # String shorthand
            {"condition": "context.value > 10", "then": {"agent": "AgentHigh", "params": {"threshold": 10}}} # Dict shorthand
        ],
        "else": {"name": "else_step", "agent": "AgentFallback"} # Full definition for else
    }]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    parent_step = steps[0]
    assert parent_step.name == "conditional_parent"
    assert len(parent_step.conditional) == 3 # Two conditions + else

    # Branch 1 (String shorthand normalization)
    branch1 = parent_step.conditional[0]
    assert isinstance(branch1, ConditionalBranch)
    assert branch1.condition == "context.status == 'ok'"
    assert isinstance(branch1.step, Step)
    assert branch1.step.name == "conditional_parent_then_0_branch" # Default name generated
    assert branch1.step.agent == "AgentOK"
    assert not branch1.step.params

    # Branch 2 (Dict shorthand normalization)
    branch2 = parent_step.conditional[1]
    assert isinstance(branch2, ConditionalBranch)
    assert branch2.condition == "context.value > 10"
    assert isinstance(branch2.step, Step)
    assert branch2.step.name == "conditional_parent_then_1_branch" # Default name generated
    assert branch2.step.agent == "AgentHigh"
    assert branch2.step.params == {"threshold": 10}

    # Branch 3 (Else branch)
    branch3 = parent_step.conditional[2]
    assert isinstance(branch3, ConditionalBranch)
    assert branch3.condition == "else"
    assert isinstance(branch3.step, Step)
    assert branch3.step.name == "else_step" # Name provided in DSL
    assert branch3.step.agent == "AgentFallback"

def test_dynamic_subtree_handled():
    """Tests parsing a step with a dynamic subtree definition."""
    dsl = {"steps": [{
        "name": "dynamic_launcher",
        "agent": "GeneratorAgent", # An agent can co-exist
        "dynamic_subtree": {"generator_type": "workflow_X", "config_param": "value1"}
    }]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    step = steps[0]
    assert step.name == "dynamic_launcher"
    assert step.agent == "GeneratorAgent"
    assert step.dynamic_subtree is not None
    assert step.dynamic_subtree == {"generator_type": "workflow_X", "config_param": "value1"}

# ==================================
# Error Handling Tests
# ==================================

def test_invalid_dsl_type():
    """Tests error if the top-level DSL is not a dictionary."""
    with pytest.raises(DSLParseError, match="Workflow DSL must be a dictionary"):
        parse_workflow([]) # type: ignore

def test_missing_steps_key():
    """Tests error if the 'steps' key is missing."""
    with pytest.raises(DSLParseError, match="must contain a 'steps' key"):
        parse_workflow({"other_key": []})

def test_invalid_steps_type():
    """Tests error if 'steps' is not a list."""
    with pytest.raises(DSLParseError, match="'steps' key with a list"):
        parse_workflow({"steps": {"name": "s1"}}) # type: ignore

def test_empty_steps_list():
    """Tests error if the 'steps' list is empty."""
    with pytest.raises(DSLParseError, match="cannot be empty"):
        parse_workflow({"steps": []})

def test_invalid_step_type_in_list():
    """Tests error if an item in 'steps' is not a dictionary."""
    with pytest.raises(DSLParseError, match="Each step definition must be a dictionary"):
        parse_workflow({"steps": [123]})

def test_step_missing_name():
    """Tests error if a step dictionary is missing the 'name' key."""
    with pytest.raises(DSLParseError, match="must have a valid 'name' field"):
        parse_workflow({"steps": [{"agent": "AgentA"}]})

def test_step_invalid_name_type():
    """Tests error if a step's 'name' is not a string."""
    with pytest.raises(DSLParseError, match="must have a valid 'name' field"):
        parse_workflow({"steps": [{"name": 123, "agent": "AgentA"}]})

def test_step_missing_action():
    """Tests error if a step has no agent, parallel, conditional, or dynamic_subtree."""
    with pytest.raises(DSLParseError, match="Step must define at least one of: 'agent'"):
        # The error message includes the list of expected keys
        parse_workflow({"steps": [{"name": "inactive_step"}]})

def test_invalid_agent_type():
    """Tests error if 'agent' is not a string."""
    with pytest.raises(DSLParseError, match="'agent' must be a string"):
        parse_workflow({"steps": [{"name": "s1", "agent": ["AgentA"]}]}) # type: ignore

def test_invalid_params_type():
    """Tests error if 'params' is not a dictionary."""
    with pytest.raises(DSLParseError, match="'params' must be a dictionary"):
        parse_workflow({"steps": [{"name": "s1", "agent": "AgentA", "params": "p1=v1"}]}) # type: ignore

def test_invalid_parallel_type():
    """Tests error if 'parallel' is not a list."""
    with pytest.raises(DSLParseError, match="'parallel' must be a list"):
        parse_workflow({"steps": [{"name": "p_parent", "parallel": {"name": "p1"}}]}) # type: ignore

def test_invalid_parallel_item_type():
    """Tests error if an item inside 'parallel' is not a dictionary."""
    with pytest.raises(DSLParseError, match="Error parsing step within 'parallel' list"):
        parse_workflow({"steps": [{"name": "p_parent", "parallel": [123]}]})

def test_invalid_conditional_type():
    """Tests error if 'conditional' is not a list."""
    with pytest.raises(DSLParseError, match="'conditional' must be a list"):
        parse_workflow({"steps": [{"name": "c_parent", "conditional": {"cond": "a>b"}}]}) # type: ignore

def test_invalid_conditional_item_type():
    """Tests error if an item inside 'conditional' is not a dictionary."""
    with pytest.raises(DSLParseError, match="Each entry in 'conditional' list .* must be a dictionary"):
        parse_workflow({"steps": [{"name": "c_parent", "conditional": ["cond"]}]})

def test_conditional_item_missing_condition():
    """Tests error if a conditional item lacks the 'condition' key."""
    with pytest.raises(DSLParseError, match="Invalid or missing 'condition'"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"then": "AgentA"}]}]})

def test_conditional_item_invalid_condition_type():
    """Tests error if a conditional item's 'condition' is not a string."""
    with pytest.raises(DSLParseError, match="Invalid or missing 'condition'"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": 123, "then": "AgentA"}]}]})

def test_conditional_item_missing_then():
    """Tests error if a conditional item lacks the 'then' key."""
    with pytest.raises(DSLParseError, match="Missing 'then' branch"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "x>0"}]}]})

def test_conditional_item_invalid_then_type():
    """Tests error if a conditional item's 'then' branch is not a string or dict."""
    with pytest.raises(DSLParseError, match="Invalid branch specification \(must be string or dictionary\)"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "x>0", "then": 123 }]}]})

def test_conditional_else_inside_list():
    """Tests error if 'condition: else' is used within the 'conditional' list."""
    with pytest.raises(DSLParseError, match="'condition: else' is not allowed within the 'conditional' list"):
         parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "else", "then": "AgentA"}]}]})

def test_invalid_else_type():
    """Tests error if the top-level 'else' value is not a string or dict."""
    with pytest.raises(DSLParseError, match="Invalid branch specification \(must be string or dictionary\)"):
        parse_workflow({"steps": [{"name": "c", "conditional": [], "else": 123 }]})

def test_invalid_dynamic_subtree_type():
    """Tests error if 'dynamic_subtree' is not a dictionary."""
    with pytest.raises(DSLParseError, match="'dynamic_subtree' must be a dictionary"):
        parse_workflow({"steps": [{"name": "d", "dynamic_subtree": ["a", "b"]}]}) # type: ignore


# ==================================
# Warning Tests
# ==================================

def test_unknown_key_warning():
    """Tests that a warning is issued for unknown keys in a step."""
    dsl = {"steps": [{
        "name": "s1",
        "agent": "AgentA",
        "extra_field": "some_value", # This should trigger the warning
        "another_unknown": 123
    }]}
    # Use pytest.warns to catch the specific warning
    with pytest.warns(SyntaxWarning, match=r"Step 's1': Unknown keys \{'.*extra_field.*', '.*another_unknown.*'\} found and will be ignored."):
        steps = parse_workflow(dsl)

    # Also check that the step was parsed correctly otherwise
    assert len(steps) == 1
    assert steps[0].name == "s1"
    assert steps[0].agent == "AgentA"
    # The unknown fields should not be part of the Step object
    assert not hasattr(steps[0], "extra_field")