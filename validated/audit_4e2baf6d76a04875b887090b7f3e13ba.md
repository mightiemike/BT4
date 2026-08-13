### Title
Metering balance under-deduction when capability spend exceeds earmarked reservation - (File: core/services/workflows/metering/metering.go)

### Summary
The workflow-engine `Report.Settle` function refunds unused, earmarked universal credits back to the local in-execution balance by computing `step.Deduction.Sub(spentCredits)` and passing it to `balanceStore.Add`. When the actual reported spend from a capability (`spentCredits`) exceeds the amount originally earmarked/deducted for that step (`step.Deduction`), this difference is negative. `balanceStore.Add` explicitly rejects negative amounts with `ErrInvalidAmount` and performs no balance mutation, so the excess spend is silently dropped from the running `balance` — only logged at info level as "invariant: spend exceeded reserve" — while `spent` bookkeeping (`AddSpent`) is still updated with the full `spentCredits`. This mirrors the reported Backd bug class: an accounting update is applied unconditionally/incorrectly around a boundary condition (spend vs. earmarked-cap), letting the tracked "available" pool diverge from the true, capped total.

### Finding Description
In `core/services/workflows/metering/metering.go`, `Deduct` earmarks credits from the local balance up front via `balanceStore.Minus` (or `ByDerivedAvailability`), and `Settle` is expected to true-up the balance based on what was actually spent by the capability DON, reported through `capabilities.ResponseMetadata.Metering`: [1](#0-0) 

The refund path assumes `step.Deduction >= spentCredits`. When a capability reports resource spend larger than what was earmarked for the step (e.g. gas spend, or per-node aggregated values via `medianSpend`/`CapDON_N` multiplication), `step.Deduction.Sub(spentCredits)` becomes negative.

`balanceStore.Add` treats any negative input as invalid and refuses to apply it: [2](#0-1) 

Because the error from `Add` is only logged, not propagated or handled, the intended "extra deduction" for the overspend never happens. The local `balance` therefore retains credits that should have been consumed, while `AddSpent(spentCredits)` still records the full (higher) actual spend for reporting purposes — creating a divergence between the enforced local spending cap (`balance`) and the true resource consumption, analogous to the reported bug where an accumulator update bypassed the intended cap-crossing correction.

### Impact Explanation
The local `balance` gates subsequent `Deduct` calls in the same workflow execution via `balanceStore.Minus`/`MinusAs`, which return `ErrInsufficientBalance` once `balance` is exhausted. If overspend on one step silently fails to reduce `balance`, later steps in the same execution can draw on credits that should already have been consumed, letting a workflow execution spend beyond its billing-service-reserved credit limit before the local cap kicks in. This is a workflow/capability spend-reporting trust boundary issue (capabilities/nodes control `ResponseMetadata.Metering` values), and its effect is to make the enforced spend limit non-deterministic/inaccurate rather than a hard cap, similar in nature to the referenced report's "total supply is not guaranteed."

### Likelihood Explanation
This path is reached whenever a step's actual reported spend (post aggregation and `CapDON_N` multiplication for non-gas spend types) exceeds the amount earmarked in `Deduct`. This can legitimately occur — e.g. concurrent-call-derived limits underestimate true multi-node cost, or gas spend units exceed the earmark — making the condition reachable in normal operation, not requiring a compromised node, since `Deduct`'s earmark (`getMaxSpendForInvocation`) is only an estimate/derived cap, and `Settle` aggregates real reported spends afterward.

### Recommendation
In `Report.Settle`, handle the overspend case explicitly instead of relying on `balanceStore.Add` to reject negative refunds silently:
- If `spentCredits > step.Deduction`, call `balanceStore.Minus(spentCredits.Sub(step.Deduction))` to properly deduct the excess (clamping at zero / switching to metering mode if it would go negative), instead of calling `Add` with a negative value.
- Ensure any failure to reconcile the balance surfaces as a metering-mode switch (as already done elsewhere in this file via `switchToMeteringMode`) rather than only an info-level log, so downstream spend limits reflect reality.

### Proof of Concept
1. Configure a report with a rate card and reserve credits.
2. Call `Deduct("step1", ByResource(unit, "", small_amount))`, earmarking `small_amount` and deducting it from `balance`.
3. Call `Settle("step1", ResponseMetadata{Metering: [...]})` with a `SpendValue` that converts to more credits than `small_amount` (e.g., via `CapDON_N` multiplication of an aggregated spend).
4. Observe `balanceStore.Add(step.Deduction.Sub(spentCredits))` returns `ErrInvalidAmount` (logged only), `balance` is unchanged from before the earmark refund, while `AddSpent` records the larger true spend — leaving `balance` higher than it should be relative to actual consumption, in contrast to `GetSpent()`/receipt data used for billing.

### Citations

**File:** core/services/workflows/metering/metering.go (L510-519)
```go

	// Refund the difference between what local balance had been earmarked and the actual spend
	if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
		// invariant: capability should not let spend exceed reserve
		r.lggr.Info("invariant: spend exceeded reserve")
	}

	r.balance.AddSpent(spentCredits)

	return nil
```

**File:** core/services/workflows/metering/balance_store.go (L171-183)
```go
// Add increases the current credit balance.
func (bs *balanceStore) Add(amount decimal.Decimal) error {
	bs.mu.Lock()
	defer bs.mu.Unlock()

	if amount.LessThan(decimal.Zero) {
		return ErrInvalidAmount
	}

	bs.balance = bs.balance.Add(amount)

	return nil
}
```
