### Title
Overly broad substring-based error classification can cause premature, permanent abandonment of legitimate workflow activations - ([File: core/services/workflows/syncer/v2/activation_errors.go])

### Summary
`isPermanentEngineInitError` in `core/services/workflows/syncer/v2/activation_errors.go` classifies a `WorkflowActivated` engine-initialization error as permanent (non-retryable) purely by doing case-insensitive substring matching (e.g. `"interval exceeded"`, `"invalid workflow id"`, `"workflowid mismatch"`) against the flattened, unwrapped error chain, rather than checking for specific sentinel error types. This mirrors the GMX finding's root cause: broad/imprecise error classification that fails to distinguish the intended narrow class of errors from superficially similar ones, causing an operation that should be retried to instead be permanently cancelled/abandoned.

### Finding Description
`classifyActivationError` [1](#0-0)  determines whether a failed `WorkflowActivated` event handling should be retried (`ActivationRetryable`) or permanently dropped (`ActivationNonRetryable`). It delegates to `isPermanentEngineInitError`, which walks the unwrapped error chain and matches lower-cased substrings such as `"workflowid mismatch"`, `"invalid workflow id"`, `"invalid cron schedule"`, and `"interval exceeded"` against the error text: [2](#0-1) .

This classification result feeds directly into `syncUsingReconciliationStrategy`, where a `ActivationNonRetryable` verdict causes the event to be abandoned immediately via `abandonActivation` instead of being scheduled for retry: [3](#0-2) .

The specific errors this is meant to catch originate from `tryEngineCreate` as narrowly-scoped, intentional `nonRetryable(...)` wraps for genuine user/config mistakes (bad workflow ID, owner decode failure, workflow ID/hash mismatch, invalid workflow name): [4](#0-3) . However, `isPermanentEngineInitError` is applied to the *entire* wrapped error returned from engine initialization — including errors surfaced from inside the WASM engine's own initialization (`initErr` from `initDone`), which is wrapped generically as `"engine initialization failed: %w"` [5](#0-4)  — and it matches by substring text rather than by a typed/sentinel error check. As documented by the project's own tests, generic phrases such as `"engine initialization failed: cron trigger interval exceeded"` are classified non-retryable purely because they contain the substring `"interval exceeded"` [6](#0-5) .

Because the match is on raw error text rather than a distinguishable error type, any transient or unrelated failure whose message happens to contain one of these generic substrings (e.g., a capability/network/rate-limiting error containing the phrase "interval exceeded", or any downstream library error containing "invalid workflow id"-like text) will be misclassified as a permanent, non-retryable failure. This is directly analogous to the GMX bug: the code intends to allow retries for legitimate transient failures, but an imprecise/overbroad detection mechanism (string matching on revert/error text rather than exact error identity) instead routes the event down the "cancel" path.

### Impact Explanation
When a `WorkflowActivated` event is misclassified as non-retryable, `abandonActivation` is invoked immediately, and the workflow will never be retried by that reconciliation loop for what could be a purely transient issue (e.g., a flaky downstream dependency whose error text happens to overlap with the substrings checked). This causes legitimate workflow activations to be permanently dropped/cancelled instead of being retried with backoff, which is a workflow/job ingestion trust-boundary misreporting/state-transition risk: an unprivileged workflow owner's otherwise-valid activation can be starved (denial of activation) purely due to unrelated errors' text overlapping with the substring list, with no way to recover except waiting for another activation event or intervention.

### Likelihood Explanation
Likelihood is moderate: it does not require a malicious actor — any legitimate transient error (e.g. rate-limiter, capability registry contention, network hiccup) whose message text happens to contain one of the generic substrings (`"interval exceeded"` is especially broad and plausible in throttling/rate-limit contexts unrelated to cron) will trigger this misclassification. The bug is latent and will manifest whenever wrapped/underlying library or capability errors use similar wording, which is outside the control of this code and can silently change over time as dependencies evolve their error messages.

### Recommendation
Replace substring/text matching in `isPermanentEngineInitError` with typed sentinel errors or explicit `errors.Is`/`errors.As` checks scoped strictly to the known permanent-failure sites in `tryEngineCreate` (which already use `nonRetryable(...)` wrapping for that exact purpose). Do not attempt to also classify opaque/unwrapped engine-initialization errors (`initErr`) via generic text heuristics; wrap only known-permanent conditions explicitly with `nonRetryable(...)` at their point of origin so that `classifyActivationError`'s `errors.As(err, &policyErr)` path (already present) is the sole source of truth, and default all other, unrecognized errors to `ActivationRetryable`.

### Proof of Concept
1. Have `tryEngineCreate`'s engine initialization (`initDone` channel) return an error whose message contains an unrelated occurrence of one of the matched substrings, e.g. a capability call error such as `"rate limiter: retry interval exceeded, please slow down"`.
2. This gets wrapped as `fmt.Errorf("engine initialization failed: %w", initErr)` in `tryEngineCreate` [7](#0-6) .
3. `activationRetryPolicyForEvent` → `classifyActivationError` → `isPermanentEngineInitError` matches on `"interval exceeded"` and returns `ActivationNonRetryable`, exactly as demonstrated in the project's existing test case for this substring: [6](#0-5) .
4. `syncUsingReconciliationStrategy` then calls `abandonActivation` with `ACTIVATION_ABANDON_REASON_NON_RETRYABLE` immediately, permanently dropping the activation instead of retrying it with backoff [8](#0-7) , even though the underlying condition was transient and unrelated to any actual cron/workflow-ID configuration mistake.

### Citations

**File:** core/services/workflows/syncer/v2/activation_errors.go (L61-77)
```go
func classifyActivationError(err error) ActivationRetryPolicy {
	var policyErr *activationPolicyError
	if errors.As(err, &policyErr) {
		return policyErr.policy
	}

	if errors.Is(err, types.ErrGlobalWorkflowCountLimitReached) ||
		errors.Is(err, types.ErrPerOwnerWorkflowCountLimitReached) {
		return ActivationNonRetryable
	}

	if isPermanentEngineInitError(err) {
		return ActivationNonRetryable
	}

	return ActivationRetryable
}
```

**File:** core/services/workflows/syncer/v2/activation_errors.go (L86-105)
```go
// isPermanentEngineInitError identifies engine init failures caused by user/config
// mistakes. Prefer sentinel errors at the source; extend this set as errors are catalogued.
func isPermanentEngineInitError(err error) bool {
	for err != nil {
		msg := strings.ToLower(err.Error())
		switch {
		case strings.Contains(msg, "workflowid mismatch"),
			strings.Contains(msg, "invalid workflow id"),
			strings.Contains(msg, "invalid workflow name"),
			strings.Contains(msg, "failed to decode workflow spec binary"),
			strings.Contains(msg, "failed to decode owner"),
			strings.Contains(msg, "invalid cron schedule"),
			strings.Contains(msg, "cron schedule must specify"),
			strings.Contains(msg, "interval exceeded"):
			return true
		}
		err = errors.Unwrap(err)
	}
	return false
}
```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L956-967)
```go
						handleErr := w.handleWithMetrics(ctx, evt.Event)
						if handleErr != nil {
							policy := activationRetryPolicyForEvent(evt.Name, handleErr)
							if policy == ActivationNonRetryable && evt.Name == WorkflowActivated {
								w.abandonActivation(ctx, sourceIdentifier, sourceName, evt, eventsv2.ActivationAbandonReason_ACTIVATION_ABANDON_REASON_NON_RETRYABLE, handleErr)
								return
							}
							evt.scheduleRetry(w.clock, w.retryInterval, w.maxRetryInterval, true)
							if evt.Name == WorkflowActivated && activationRetriesExhausted(evt.retryCount, w.maxActivationRetries) {
								w.abandonActivation(ctx, sourceIdentifier, sourceName, evt, eventsv2.ActivationAbandonReason_ACTIVATION_ABANDON_REASON_RETRY_LIMIT_EXCEEDED, handleErr)
								return
							}
```

**File:** core/services/workflows/syncer/v2/handler.go (L920-953)
```go
	decodedBinary, err := hex.DecodeString(spec.Workflow)
	if err != nil {
		return nonRetryable(fmt.Errorf("failed to decode workflow spec binary: %w", err))
	}
	// Free the hex-encoded binary string as it is not needed beyond this decode
	spec.Workflow = ""

	// Workflow Registry version >2 no longer handles secrets
	secretsURL := ""

	// Before running the engine, handle validations
	// Workflow ID should match what is generated from the stored artifacts
	ownerBytes, err := hex.DecodeString(spec.WorkflowOwner)
	if err != nil {
		return nonRetryable(fmt.Errorf("failed to decode owner: %w", err))
	}
	configBytes := []byte(spec.Config)
	hash, err := pkgworkflows.GenerateWorkflowID(ownerBytes, spec.WorkflowName, decodedBinary, configBytes, secretsURL)
	if err != nil {
		return fmt.Errorf("failed to generate workflow id: %w", err)
	}
	wid, err := types.WorkflowIDFromHex(spec.WorkflowID)
	if err != nil {
		return nonRetryable(fmt.Errorf("invalid workflow id: %w", err))
	}
	if !types.WorkflowID(hash).Equal(wid) {
		return nonRetryable(fmt.Errorf("workflowID mismatch: %x != %x", hash, wid))
	}

	// Start a new WorkflowEngine instance, and add it to local engine registry
	workflowName, err := types.NewWorkflowName(spec.WorkflowName)
	if err != nil {
		return nonRetryable(fmt.Errorf("invalid workflow name: %w", err))
	}
```

**File:** core/services/workflows/syncer/v2/handler.go (L979-986)
```go
	case initErr := <-initDone:
		if initErr != nil {
			// Engine initialization failed (e.g., trigger subscription failed)
			if closeErr := engine.Close(); closeErr != nil {
				h.lggr.Errorw("failed to close engine after initialization failure", "error", closeErr, "workflowID", spec.WorkflowID)
			}
			return fmt.Errorf("engine initialization failed: %w", initErr)
		}
```

**File:** core/services/workflows/syncer/v2/activation_errors_test.go (L56-60)
```go
		{
			name: "interval exceeded is non-retryable",
			err:  fmt.Errorf("engine initialization failed: %w", errors.New("cron trigger interval exceeded")),
			want: ActivationNonRetryable,
		},
```
