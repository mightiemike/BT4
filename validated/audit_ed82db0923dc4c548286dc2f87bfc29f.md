### Title
Metering `Report.Deduct`/`Settle` credit-balance accounting is inconsistent on failed deductions, allowing balance inflation - ([File: core/services/workflows/metering/metering.go])

### Summary
The Mochi report describes a debt-accounting bug where one code path increments a ledger variable by a value excluding a fee while a different path decrements it by a value including the fee, causing the ledger to drift from the true balance. The Chainlink CRE workflow metering package (`core/services/workflows/metering/metering.go`) has an analogous bug: `Report.Deduct` records the intended deduction amount into `step.Deduction` unconditionally, even when the underlying balance mutation (`balanceStore.Minus`) fails and does not actually change the balance. `Report.Settle` later uses that recorded (but never-applied) `step.Deduction` value to credit the balance back, causing the balance to be inflated with credits that were never actually reserved.

### Finding Description
`ByResource` (and `ByDerivedAvailability`) set `step.Deduction = bal` (the amount to deduct) and register the step into `r.steps[ref]` via a `defer`, *before* checking whether `r.balance.Minus(bal)` succeeded: [1](#0-0) 

`balanceStore.Minus` refuses to mutate the balance and returns `ErrInsufficientBalance` when the requested amount exceeds the current balance, i.e. the balance is left completely unchanged on failure: [2](#0-1) 

Despite the failed/no-op subtraction, the `defer` still stores `step.Deduction = bal` into `r.steps[ref]`, so the report now believes `bal` was earmarked from the balance when in fact it was not.

Later, `Report.Settle` is called for that same `ref` — the workflow engine is explicitly designed to continue and call `Settle` even when `Deduct` returned `ErrInsufficientBalance` ("fail open"), as shown by the test `happy path with zero reserve and insufficient balance does not block workflow execution`: [3](#0-2) 

`Settle` then refunds the *unspent portion* of `step.Deduction` back into the balance: [4](#0-3) 

Because `step.Deduction` (`bal`) was never actually subtracted from `r.balance` in the failed `Deduct` call, `r.balance.Add(step.Deduction.Sub(spentCredits))` injects a phantom credit of `bal - spentCredits` into the balance — money that was never really reserved is now being "returned," inflating the account balance beyond what it should be. This mirrors the Mochi pattern precisely: one code path (`Deduct`, on the failure branch) does not perform the balance-decreasing side effect for the recorded value, while the other path (`Settle`) assumes symmetry and undoes a deduction that never happened.

### Impact Explanation
This causes the local in-memory credit balance tracked in the `Report` (and ultimately the credits reported via `SendReceipt`/`FormatReport` to the billing service) to diverge from the true amount that should have been consumed. Since this balance directly drives spend-limit derivation (`getMaxSpendForInvocation`, `ByDerivedAvailability`) for subsequent steps in the same workflow execution, an inflated balance lets later steps spend more universal credits than they should have been entitled to, resulting in under-billing / incorrect credit accounting for workflow executions. This is a data-tampering/misreporting-class impact on the CRE billing/metering subsystem, not merely a cosmetic display bug.

### Likelihood Explanation
The vulnerable path requires a `Deduct` call that hits `ErrInsufficientBalance` (or other `Minus`/`MinusAs` failure) mid-execution while credits remain low, followed by a normal `Settle` call for the same step — which is the documented "fail open" behavior of the engine (workflow execution continues on insufficient balance). This is a realistic, unprivileged, and reachable execution path in any CRE workflow whose reserved balance is exhausted partway through execution, not requiring any malicious actor.

### Recommendation
Only set/record `step.Deduction` (and thus reuse it for the Settle-time refund calculation) when the underlying balance mutation actually succeeded. On failure, `step.Deduction` should remain `decimal.Zero` (or the step should be marked so `Settle` performs no phantom refund for that reference), keeping the recorded earmark consistent with the real balance state — analogous to fixing the Mochi bug by making the increment/decrement of the ledger variable always reflect the same underlying quantity.

### Proof of Concept
1. Call `Report.Reserve` to set a small balance (e.g. 10 credits) as in `newTestReport`/`successReserveResponseWithRates`.
2. Call `Report.Deduct("step1", ByResource(unit, "", decimal.NewFromInt(11_000)))` with a `bal` amount exceeding the balance — this returns `ErrInsufficientBalance` but does not decrement `r.balance` (per `balanceStore.Minus`), while `step.Deduction` is still stored as the large `bal` value in `r.steps["step1"]`.
3. Call `Report.Settle("step1", metadata)` with a small actual spend (e.g. 1 credit worth of `MeteringNodeDetail`).
4. Observe `r.balance.Add(step.Deduction.Sub(spentCredits))` adds nearly the entire erroneous `bal` amount back into `r.balance`, even though that amount was never actually removed — inflating the balance well beyond its pre-`Deduct` value. [5](#0-4) [6](#0-5)

### Citations

**File:** core/services/workflows/metering/metering.go (L312-338)
```go
	return func(ref string, r *Report) ([]capabilities.SpendLimit, error) {
		step := ReportStep{
			CapabilityID:     capabilityID,
			Deduction:        decimal.Zero,
			AggregatedSpends: make(map[string]AggregatedStepDetail),
		}

		defer func() {
			r.steps[ref] = step
		}()

		bal, err := r.balance.ConvertToBalance(spendType, amount)
		if err != nil {
			// Fail open, continue optimistically
			r.switchToMeteringMode(fmt.Errorf("failed to convert to balance [%s]: %w", spendType, err))
		}

		step.Deduction = bal

		// if in metering mode, exit early without modifying local balance
		if r.meteringMode {
			return []capabilities.SpendLimit{}, nil
		}

		return []capabilities.SpendLimit{}, r.balance.Minus(bal)
	}
}
```

**File:** core/services/workflows/metering/metering.go (L506-519)
```go
	// if in metering mode, exit early without modifying local balance
	if r.meteringMode {
		return nil
	}

	// Refund the difference between what local balance had been earmarked and the actual spend
	if err := r.balance.Add(step.Deduction.Sub(spentCredits)); err != nil {
		// invariant: capability should not let spend exceed reserve
		r.lggr.Info("invariant: spend exceeded reserve")
	}

	r.balance.AddSpent(spentCredits)

	return nil
```

**File:** core/services/workflows/metering/balance_store.go (L130-146)
```go
// Minus lowers the current credit balance.
func (bs *balanceStore) Minus(amount decimal.Decimal) error {
	bs.mu.Lock()
	defer bs.mu.Unlock()

	if amount.LessThan(decimal.Zero) {
		return ErrInvalidAmount
	}

	if amount.GreaterThan(bs.balance) {
		return ErrInsufficientBalance
	}

	bs.balance = bs.balance.Sub(amount)

	return nil
}
```

**File:** core/services/workflows/metering/metering_test.go (L1440-1474)
```go
	t.Run("happy path with zero reserve and insufficient balance does not block workflow execution", func(t *testing.T) {
		t.Parallel()

		billingClient := mocks.NewBillingClient(t)
		billingClient.EXPECT().GetWorkflowExecutionRates(mock.Anything, mock.Anything).
			Return(&billing.GetWorkflowExecutionRatesResponse{
				RateCards: successRates,
			}, nil)
		billingClient.EXPECT().ReserveCredits(mock.Anything, mock.Anything).
			Return(&successZeroReserveResponseWithRates, nil)

		report := newTestReport(t, logger.Nop(), billingClient)

		require.NoError(t, report.Reserve(t.Context()))

		// Deduct and Settle a few times to consume credits
		// Each deduction of 2 units of compute consumes 1 credit (rate: 2 units per credit)
		_, err := report.Deduct("step1", ByResource(testUnitA, "", decimal.NewFromInt(2)))
		require.ErrorIs(t, err, ErrInsufficientBalance) // insufficient balance does not block workflow execution
		require.NoError(t, report.Settle("step1", capabilities.ResponseMetadata{Metering: []capabilities.MeteringNodeDetail{
			{Peer2PeerID: "node1", SpendUnit: testUnitA, SpendValue: "2"},
		}}))

		_, err = report.Deduct("step2", ByResource(testUnitA, "", decimal.NewFromInt(4)))
		require.ErrorIs(t, err, ErrInsufficientBalance)
		require.NoError(t, report.Settle("step2", capabilities.ResponseMetadata{Metering: []capabilities.MeteringNodeDetail{
			{Peer2PeerID: "node2", SpendUnit: testUnitA, SpendValue: "4"},
		}}))

		_, err = report.Deduct("step3", ByResource(testUnitA, "", decimal.NewFromInt(2)))
		require.ErrorIs(t, err, ErrInsufficientBalance)
		require.NoError(t, report.Settle("step3", capabilities.ResponseMetadata{Metering: []capabilities.MeteringNodeDetail{
			{Peer2PeerID: "node3", SpendUnit: testUnitA, SpendValue: "2"},
		}}))

```
