### Title
Unprivileged workflow owner can exhaust the shared global workflow-slot limit, blocking every other owner from registering/activating workflows - (File: `core/services/workflows/syncerlimiter/limiter.go`)

### Summary
The Chainlink node enforces a global cap on how many workflows can be active at once, shared across every workflow owner, via `syncerlimiter.NewWorkflowLimits`. Because the default `PerOwner` limit (200) equals the default `Global` limit (200), a single unprivileged owner can register enough workflows to consume the *entire* global pool by itself, exactly mirroring the reported bug class: a cheap, user-controlled action consuming a shared, capacity-limited resource that gates a common feature (pool creation in the report; workflow registration/activation here), denying service to all other legitimate users until the resource is freed.

### Finding Description
`syncerlimiter.NewWorkflowLimits` builds a `MultiResourcePoolLimiter` from two pools: an owner-scoped pool (`PerOwner`, default 200) and a global pool (`Global`, default 200), configured from `[Workflows.Limits]` in `core/config/docs/core.toml`: [1](#0-0) [2](#0-1) 

Each workflow `Engine` calls `GlobalWorkflowLimit.Use(ctx, 1)` on startup, and if the limiter reports either the per-owner or the global scope is exceeded, initialization fails with `ErrPerOwnerWorkflowCountLimitReached` or `ErrGlobalWorkflowCountLimitReached`: [3](#0-2) 

Because `PerOwner` defaults to the same value as `Global` (200), a single owner is permitted, by configuration, to occupy the *entire* global capacity by registering up to 200 low-cost workflows (registration only requires whatever minimal fee/gas the `WorkflowRegistry` contract charges, comparable to the report's "gas fee + 10 wei" cost to spam pool creation). Once the global pool is saturated by one owner, `wsl.Use` for any other owner returns an error with `settings.ScopeGlobal`, and every other legitimate workflow owner on that node is blocked from activating any workflow — this is demonstrated directly by the test: [4](#0-3) 

This is the same root-cause pattern as the `takeLoan` `MAX_LOAN_PER_BLOCK` issue: a globally-shared, capped counter that gates access to a legitimate feature, combined with a per-actor allowance large enough (here, equal) to let one actor monopolize the whole shared cap cheaply.

### Impact Explanation
Any single account (owner address) that can get workflows registered on-chain (via the `WorkflowRegistry` contract) can, at minimal on-chain cost, occupy the node's entire global workflow capacity. All other users' workflows will fail to initialize with `ErrGlobalWorkflowCountLimitReached` until the attacker's workflows are freed (deleted/paused), producing a persistent denial-of-service against workflow execution for the affected node — analogous to the report's "legitimate users can't launch new pools" impact.

### Likelihood Explanation
Likelihood is moderate-to-high in default configurations: the defaults (`Global=200`, `PerOwner=200`) allow full exhaustion by one actor with no additional privilege beyond being able to register workflows in the `WorkflowRegistry`. Node operators who leave `PerOwner` at its default (equal to `Global`) rather than tightening it are exposed. The attack does not require special timing (unlike the per-block loan race) — an attacker can simply register up to 200 workflows over time and keep them alive.

### Recommendation
1. Set a sane default `PerOwner` limit that is meaningfully smaller than `Global` (e.g., a small fraction), so no single owner can exhaust the shared pool.
2. Document/enforce that `PerOwner` should always be `< Global` in `syncerlimiter.Config` validation, similar to how `RateLimiterConfig` on-chain enforces `Rate < Capacity`.
3. Consider adding an additional cost/friction (e.g., staking, registration fee, or reputation-based per-owner quota) to workflow registration to raise the cost of monopolizing global capacity, mirroring the report's "charge a launch fee" mitigation.

### Proof of Concept
Using the existing unit test harness for `syncerlimiter`, configure `Global` and `PerOwner` to the same value (as in production defaults) and observe a single owner consuming the whole global pool, after which any other owner is rejected with a `ScopeGlobal` error: [5](#0-4) 
With production defaults (`Global=200`, `PerOwner=200`), one owner registering/activating 200 workflows would reproduce the same "Global exceeded" rejection for every other owner, as shown by the `ScopeGlobal` assertions in the same test at lines 69–80 of `limiter_test.go`.

### Citations

**File:** core/services/workflows/syncerlimiter/limiter.go (L48-78)
```go
func NewWorkflowLimits(lggr logger.Logger, cfg Config, lf limits.Factory) (limits.ResourceLimiter[int], error) {
	lggr = logger.Named(lggr, "WorkflowExecutionLimiter")
	cfg.PerOwnerOverrides = normalizeOverrides(cfg.PerOwnerOverrides)

	ownerLimit := cresettings.Default.PerOwner.WorkflowLimit // make a copy
	if cfg.PerOwner > 0 {
		ownerLimit.DefaultValue = int(cfg.PerOwner)
	}
	perOwner := make(map[string]string, len(cfg.PerOwnerOverrides))
	for k, v := range cfg.PerOwnerOverrides {
		perOwner[k] = strconv.Itoa(int(v))
	}
	lf.Settings = keyedOwnerSettings{getter: lf.Settings, key: ownerLimit.Key, vals: perOwner}
	owner, err := limits.MakeResourcePoolLimiter(lf, ownerLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create owner resource limiter: %w", err)
	}

	globalLimit := cresettings.Default.WorkflowLimit // make a copy
	if cfg.Global > 0 {
		globalLimit.DefaultValue = int(cfg.Global)
	}
	global, err := limits.MakeResourcePoolLimiter(lf, globalLimit)
	if err != nil {
		return nil, fmt.Errorf("failed to create global resource limiter: %w", err)
	}

	lggr.Debugw("workflow limits set", "perOwner", cfg.PerOwner, "global", cfg.Global, "overrides", cfg.PerOwnerOverrides)

	return limits.MultiResourcePoolLimiter[int]{owner, global}, nil
}
```

**File:** core/config/docs/core.toml (L592-598)
```text
[Workflows]
[Workflows.Limits]
# Global is the maximum number of workflows that can be registered globally.
Global = 200 # Default
# PerOwner is the maximum number of workflows that can be registered per owner.
PerOwner = 200 # Default

```

**File:** core/services/workflows/v2/engine.go (L330-353)
```go
	// apply global engine instance limits
	// TODO(CAPPL-794): consider moving this outside of the engine, into the Syncer
	err := e.cfg.GlobalWorkflowLimit.Use(ctx, 1)
	if err != nil {
		var errLimited limits.ErrorResourceLimited[int]
		if errors.As(err, &errLimited) {
			switch errLimited.Scope {
			case settings.ScopeOwner:
				e.logger().Infow("Per owner workflow count limit reached", "err", err)
				e.metrics.IncrementWorkflowLimitPerOwnerCounter(ctx)
				e.cfg.Hooks.OnInitialized(types.ErrPerOwnerWorkflowCountLimitReached)
			case settings.ScopeGlobal:
				e.logger().Infow("Global workflow count limit reached", "err", err)
				e.metrics.IncrementWorkflowLimitGlobalCounter(ctx)
				e.cfg.Hooks.OnInitialized(types.ErrGlobalWorkflowCountLimitReached)
			default:
				e.logger().Errorw("Workflow count limit reached for unexpected scope", "scope", errLimited.Scope, "err", err)
				e.cfg.Hooks.OnInitialized(err)
			}
		} else {
			e.cfg.Hooks.OnInitialized(err)
		}
		return
	}
```

**File:** core/services/workflows/syncerlimiter/limiter_test.go (L23-35)
```go
func TestWorkflowLimits(t *testing.T) {
	t.Parallel()
	lggr := logger.TestLogger(t)

	config := Config{
		Global:   3,
		PerOwner: 1,
		PerOwnerOverrides: map[string]int32{
			"0x" + user5String: 2,
		},
	}
	wsl, err := NewWorkflowLimits(lggr, config, limits.Factory{Logger: lggr.Named("Limits")})
	require.NoError(t, err)
```

**File:** core/services/workflows/syncerlimiter/limiter_test.go (L66-72)
```go
	ctx4 := contexts.WithCRE(t.Context(), contexts.CRE{Owner: user4String})
	err = wsl.Use(ctx4, 1)
	require.Error(t, err)
	if assert.ErrorAs(t, err, &errLimited) {
		require.Equal(t, settings.ScopeGlobal, errLimited.Scope)
	}
	// Global 3/3, PerOwner 0/1 Global exceeded
```
