# Advanced Learning Capabilities

This note dives deeper into the advanced learning hooks available in
`agentic_codex`, focusing on reinforcement learning and JEPA-style alignment.

## ReinforcementLearningCapability

`ReinforcementLearningCapability` wires a trainer object (or callable) into the
agent kernel's learn loop.

### Key Parameters
- **trainer**: Implements `update(context, step, environment=None)` or an
  equivalent signature.
- **environment**: Arbitrary metadata (reward targets, budgets, knobs) passed to
  each update.
- **auto_setup**: When `True`, calls `trainer.setup(...)` during kernel
  initialization.

### Sample Usage
```python
trainer = MyTrainer()
agent = (
    AgentBuilder(name="learner", role="rl")
    .with_reinforcement_learning(trainer, environment={"reward": 0.8})
    .with_step(step_fn)
    .build()
)
```

### Tips
1. Keep `trainer.update` side-effect free beyond trainer/context state updates.
2. Log reward or adjustment events via `context.scratch` so downstream
   evaluators can cite them.
3. Combine with JEPA (below) when you need joint representation + policy
   updates.

## JEPALearningCapability

`JEPALearningCapability` adds a joint embedding predictive architecture style
learner. Each learn pass:

1. Evaluates a **predictor** callable.
2. Evaluates a **target** callable.
3. Computes a **loss** via `loss_fn(predictor, target)`.
4. Appends the tuple to `context.scratch[state_key]`.
5. Calls `update_fn(context, predictor, target, loss)` for custom adjustments.

### Parameters
- **predictor**: Callable returning representations from current context.
- **target**: Callable returning business-guided targets or delayed embeddings.
- **loss_fn**: Produces a scalar alignment loss.
- **update_fn**: Applies corrective logic (e.g., adjust hyperparameters).
- **state_key**: Scratch key storing JEPA history (defaults to
  `"jepa_history"`).

### Example
```python
def predictor(ctx):
    return ctx.scratch["model_outputs"]["metrics"]["rmse"]

def target(ctx):
    return ctx.scratch["context"]["hyperparameters"]["target_rmse"]

def loss_fn(pred, tgt):
    return abs(pred - tgt)

def update_fn(ctx, pred, tgt, loss):
    ctx.scratch.setdefault("jepa_updates", []).append({"loss": loss})

agent = (
    AgentBuilder(name="rep-align", role="analyst")
    .with_jepa_learning(predictor, target, loss_fn, update_fn)
    .with_step(step_fn)
    .build()
)
```

### Tips
- Use JEPA to align statistical quality metrics with business heuristics
  (e.g., RMSE vs. target RMSE, ROI vs. guardrails).
- Snapshot predictor, target, and loss for observability/dashboards.
- Chain JEPA with RL to keep representations and policy updates synchronized.

## Reference

- `examples/mtcars_linear_regression_agents.ipynb` – demonstrates RL + JEPA in a
  regression workflow.
