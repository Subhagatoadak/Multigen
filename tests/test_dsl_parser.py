import pytest
import warnings

from orchestrator.services.dsl_parser import (
    parse_workflow,
    parse_step,
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
    step = steps[0]
    assert isinstance(step, Step)
    assert step.name == "s1"
    assert step.agent == "AgentA"
    assert step.params == {"p1": "v1"}
    assert not step.parallel
    assert not step.conditional
    assert step.dynamic_subtree is None

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
    parent = steps[0]
    assert parent.name == "parallel_parent"
    assert parent.agent is None
    assert len(parent.parallel) == 2
    assert parent.parallel[0].name == "p1"
    assert parent.parallel[0].agent == "AgentA"
    assert parent.parallel[0].params == {"a": 1}
    assert parent.parallel[1].name == "p2"
    assert parent.parallel[1].agent == "AgentB"

def test_conditional_branches_with_normalization():
    """Tests parsing conditionals, including shorthand and else."""
    dsl = {"steps": [{
        "name": "conditional_parent",
        "conditional": [
            {"condition": "context.status == 'ok'", "then": "AgentOK"},
            {"condition": "context.value > 10", "then": {"agent": "AgentHigh", "params": {"threshold": 10}}}
        ],
        "else": {"name": "else_step", "agent": "AgentFallback"}
    }]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    parent = steps[0]
    assert parent.name == "conditional_parent"
    # two then branches + else
    assert len(parent.conditional) == 3

    b1 = parent.conditional[0]
    assert isinstance(b1, ConditionalBranch)
    assert b1.condition == "context.status == 'ok'"
    assert isinstance(b1.step, Step)
    assert b1.step.name == "conditional_parent_then_0_branch"
    assert b1.step.agent == "AgentOK"
    assert not b1.step.params

    b2 = parent.conditional[1]
    assert isinstance(b2, ConditionalBranch)
    assert b2.condition == "context.value > 10"
    assert b2.step.name == "conditional_parent_then_1_branch"
    assert b2.step.agent == "AgentHigh"
    assert b2.step.params == {"threshold": 10}

    b3 = parent.conditional[2]
    assert isinstance(b3, ConditionalBranch)
    assert b3.condition == "else"
    assert b3.step.name == "else_step"
    assert b3.step.agent == "AgentFallback"

def test_dynamic_subtree_handled():
    """Tests parsing a step with a dynamic subtree."""
    dsl = {"steps": [{
        "name": "dynamic_launcher",
        "agent": "GeneratorAgent",
        "dynamic_subtree": {"generator_type": "workflow_X", "config_param": "value1"}
    }]}
    steps = parse_workflow(dsl)
    assert len(steps) == 1
    st = steps[0]
    assert st.name == "dynamic_launcher"
    assert st.agent == "GeneratorAgent"
    assert st.dynamic_subtree == {"generator_type": "workflow_X", "config_param": "value1"}

# ==================================
# Error Handling Tests
# ==================================

def test_invalid_dsl_type():
    with pytest.raises(DSLParseError, match="Workflow DSL must be a dictionary"):
        parse_workflow([])  # type: ignore

def test_missing_steps_key():
    with pytest.raises(DSLParseError, match="must contain a 'steps' key"):
        parse_workflow({"foo": []})

def test_invalid_steps_type():
    with pytest.raises(DSLParseError, match="'steps' key with a list"):
        parse_workflow({"steps": {"name": "s1"}})  # type: ignore

def test_empty_steps_list():
    with pytest.raises(DSLParseError, match="cannot be empty"):
        parse_workflow({"steps": []})

def test_invalid_step_type_in_list():
    with pytest.raises(DSLParseError, match="must be a dictionary"):
        parse_workflow({"steps": [123]})

def test_step_missing_name():
    with pytest.raises(DSLParseError, match="must have a valid 'name' field"):
        parse_workflow({"steps": [{"agent": "AgentA"}]})

def test_step_invalid_name_type():
    with pytest.raises(DSLParseError, match="must have a valid 'name' field"):
        parse_workflow({"steps": [{"name": 123, "agent": "A"}]})

def test_step_missing_action():
    with pytest.raises(DSLParseError, match="must define at least one of"):
        parse_workflow({"steps": [{"name": "inactive"}]})

def test_invalid_agent_type():
    with pytest.raises(DSLParseError, match="'agent' must be a string"):
        parse_workflow({"steps": [{"name": "s1", "agent": ["A"]}]})  # type: ignore

def test_invalid_params_type():
    with pytest.raises(DSLParseError, match="'params' must be a dictionary"):
        parse_workflow({"steps": [{"name": "s1", "agent": "A", "params": "notadict"}]})  # type: ignore

def test_invalid_parallel_type():
    with pytest.raises(DSLParseError, match="'parallel' must be a list"):
        parse_workflow({"steps": [{"name": "p_parent", "parallel": {"name": "p1"}}]})  # type: ignore

def test_invalid_parallel_item_type():
    with pytest.raises(DSLParseError, match="within 'parallel'"):
        parse_workflow({"steps": [{"name": "p_parent", "parallel": [123]}]})

def test_invalid_conditional_type():
    with pytest.raises(DSLParseError, match="'conditional' must be a list"):
        parse_workflow({"steps": [{"name": "c_parent", "conditional": {"cond": "a>b"}}]})  # type: ignore

def test_invalid_conditional_item_type():
    with pytest.raises(DSLParseError, match="must be a dictionary"):
        parse_workflow({"steps": [{"name": "c_parent", "conditional": ["oops"]} ]})

def test_conditional_item_missing_condition():
    with pytest.raises(DSLParseError, match="Invalid or missing 'condition'"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"then": "A"}]}]})

def test_conditional_item_invalid_condition_type():
    with pytest.raises(DSLParseError, match="Invalid or missing 'condition'"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": 123, "then": "A"}]}]})

def test_conditional_item_missing_then():
    with pytest.raises(DSLParseError, match="Missing 'then' branch"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "x>0"}]}]})

def test_conditional_item_invalid_then_type():
    with pytest.raises(DSLParseError, match="Invalid branch specification"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "x>0", "then": 123}]}]})

def test_conditional_else_inside_list():
    with pytest.raises(DSLParseError, match="not allowed within the 'conditional' list"):
        parse_workflow({"steps": [{"name": "c", "conditional": [{"condition": "else", "then": "A"}]}]})

def test_invalid_else_type():
    with pytest.raises(DSLParseError, match="Invalid branch specification"):
        parse_workflow({"steps": [{"name": "c", "conditional": [], "else": 123} ]})

def test_invalid_dynamic_subtree_type():
    with pytest.raises(DSLParseError, match="'dynamic_subtree' must be a dictionary"):
        parse_workflow({"steps": [{"name": "d", "dynamic_subtree": ["a", "b"]} ]})  # type: ignore

# ==================================
# Warning Tests
# ==================================

def test_unknown_key_warning():
    """Tests that a warning is issued for unknown keys in a step."""
    dsl = {"steps": [{
        "name": "s1",
        "agent": "AgentA",
        "extra_field": "foo",
        "another_unknown": 123
    }]}
    with pytest.warns(SyntaxWarning,
                      match=r"Step 's1': Unknown keys \{'extra_field', 'another_unknown'\} found and will be ignored\."):
        steps = parse_workflow(dsl)

    assert len(steps) == 1
    assert steps[0].name == "s1"
    assert steps[0].agent == "AgentA"
    # unknown fields should not become attributes
    assert not hasattr(steps[0], "extra_field")
    assert not hasattr(steps[0], "another_unknown")
