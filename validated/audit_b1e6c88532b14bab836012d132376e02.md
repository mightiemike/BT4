### Title
Gas-spend to credit conversion truncates to zero for small amounts, allowing unbilled capability usage - ([File: core/services/workflows/metering/balance_store.go])

### Summary
`balanceStore.convertToBalance()` converts a resource-dimension spend amount (e.g. gas wei) into a universal credit amount by dividing the raw amount by the configured rate and rounding to a fixed decimal precision. Because the division result is rounded to `defaultDecimalPrecision` (10 decimal places) rather than accumulated or floored conservatively, any spend amount whose true credit value is below `1e-10` credits is silently truncated to `0`, exactly mirroring the Surge `getCurrentState()` root cause where `_interest = _totalDebt * _borrowRate * _timeDelta / (365 days * 1e18)` truncates to `0` for small-but-legitimate inputs, causing systemic non-billing.

### Finding Description
`convertToBalance` performs the gas-spend-type conversion as: [1](#0-0) 

`Report.Settle()` feeds capability-reported gas spend values into this function after shifting them into a wei-fixed-point representation: [2](#0-1) 

The gas conversion rate (`UnitsPerCredit`/`GasTokensPerCredit`) is provided by the billing service in wei-per-credit form and can be very large (e.g. `230140614074074` or `10000000000` wei/credit as seen in tests): [3](#0-2) 

When `amount` (spend in wei) is small relative to `rate` (wei per credit), `amount.Div(rate)` yields a value smaller than `1e-10`, and `.Round(defaultDecimalPrecision)` rounds it to exactly `0`: [4](#0-3) 

This is functionally identical to the Surge `Pool.sol` bug: legitimate small-but-nonzero economic values (interest there, gas-spend credits here) are integer/fixed-precision-divided against a large denominator and truncate to zero, so the state update (interest accrual there; credit deduction here) never happens for those calls even though real resource consumption occurred. The existing test suite already documents this exact behavior as "expected", e.g. a real gas spend of `0.000700000000000000` (700000000000000 wei, i.e. after `.Shift(18)`) against a rate of `0` credits per unit converting to `0` credits, and other tests show gas rates in the range that make small spends round to zero: [5](#0-4) 

### Impact Explanation
Because `Settle()` deducts `spentCredits` computed via this rounding-to-zero conversion from the earmarked balance and adds the earmarked-minus-spent difference back to the available balance: [6](#0-5) 

a workflow owner (an unprivileged CRE user, not requiring any special node/operator privilege) can structure gas-metered capability calls with per-call wei spend below the rounding threshold of the configured rate to have those calls billed as `0` credits every time, even though the capability genuinely consumed on-chain gas that the DON/node paid for. Repeated over many workflow executions, this results in real resource consumption (gas paid by chainlink nodes on behalf of the workflow) never being reflected as a credit deduction, causing the billing/metering system (and therefore Chainlink node operators who fund on-chain gas) to under-recover cost — a direct financial/fund-loss impact analogous to lenders losing interest income in the Surge issue.

### Likelihood Explanation
This requires no privileged access — any workflow author who understands (or empirically discovers, e.g. via the metering report's `SpendValueCre` field which is user-visible) that small gas amounts convert to `0` credits can deliberately keep each capability invocation's gas spend under the rounding threshold to avoid being billed. Since `GAS.*` rates are per-chain, wei-denominated, and can be numerically large relative to a single capability's marginal gas cost, this is realistically triggerable rather than purely theoretical.

### Recommendation
Do not round per-call conversions down to a fixed decimal precision when the true value is nonzero. Instead:
- Track truncated/residual fractional credit amounts across calls (similar to Surge's fix of not silently dropping small updates) and accumulate them until they exceed the minimum representable credit unit, then bill them.
- Alternatively, always round non-zero conversions up (ceiling) rather than to-nearest/down when the amount is provably nonzero, so `spend > 0` never converts to `spentCredits == 0`.
- Add an explicit test asserting that repeated small nonzero gas spends sum to a nonzero total credit deduction across a session, not zero on every call.

### Proof of Concept
Given a gas rate of `10000000000` wei/credit (as used in `metering_test.go`) and a legitimate per-call gas spend of `1 wei` reported via `MeteringNodeDetail.SpendValue` after `Shift(18)`-based normalization, `convertToBalance("GAS.<chain>", 1)` computes `1 / 10000000000 = 1e-10`, which `.Round(10)` still barely captures, but any spend below `1e-11` credits (e.g., sub-wei-equivalent amounts, or slightly larger real-world rates such as the `230140614074074` wei/credit rate also used in tests) rounds exactly to `0`. Repeating such a call N times via `Report.Deduct`/`Report.Settle` results in `spentCredits == 0` for all N calls and no credits ever deducted from `balance`, matching the existing test `"successfully settles zero value rate"` behavior at [7](#0-6) , but generalized to any nonzero small spend under a large-enough rate rather than only an explicit zero rate.

### Citations

**File:** core/services/workflows/metering/balance_store.go (L55-63)
```go
	// Special case for gas as gas token conversions are provided in amount per credit.
	// Other rates are provided as the inverse.
	if isGasSpendType(fromResourceType) {
		if rate.IsZero() {
			return decimal.Zero, nil
		}

		return amount.Div(rate).Round(defaultDecimalPrecision), nil
	}
```

**File:** core/services/workflows/metering/metering.go (L31-35)
```go
const (
	RatiosKey = "spendRatios"
	// the default decimal precision is a fixed number defined in the billing service. if this gets changed
	// in the billing service project, the value here needs to change.
	defaultDecimalPrecision = 10 // one thousandth of a dollar
```

**File:** core/services/workflows/metering/metering.go (L450-459)
```go
			if isGasSpendType(unit) {
				// TODO: this decimal shift should be temporary and converted when write capabilities
				// are converted to provide spend as big.Int fixed point values
				// WARNING: 18 is a magic number here and assumes all gas tokens will have the same level of precision
				value = value.Shift(18) // shift to fixed point value
			}

			if val, convertErr := r.balance.ConvertToBalance(unit, value); convertErr == nil {
				resourceSpends[unit][idx].CRESpendValue = val
			}
```

**File:** core/services/workflows/metering/metering.go (L490-517)
```go
		bal, err := r.balance.ConvertToBalance(unit, value)

		if err != nil {
			r.switchToMeteringMode(fmt.Errorf("attempted to Settle [%s]: %w", unit, err))
		} else {
			aggregated.CRESpendValue = bal
			spentCredits = spentCredits.Add(bal)
		}

		step.AggregatedSpends[unit] = aggregated
	}

	step.Spends = resourceSpends
	step.CapdonN = metadata.CapDON_N
	r.steps[ref] = step

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
```

**File:** core/services/workflows/metering/metering.go (L833-844)
```go
	// credits per gas are provided in the form of map[chainselector] -> <gasRate>string
	// each entry should be converted to a usable rate card with form of GAS.[chainselector] -> <unitsPerCredit>decimal
	gasCredits := resp.GetGasTokensPerCredit()

	for chainSelector, gasRate := range gasCredits {
		conversionDeci, err := decimal.NewFromString(gasRate)
		if err != nil {
			return map[string]decimal.Decimal{}, fmt.Errorf("could not convert gas rate %d's value %s to decimal", chainSelector, gasRate)
		}

		rateCard[fmt.Sprintf("GAS.%d", chainSelector)] = conversionDeci
	}
```

**File:** core/services/workflows/metering/metering_test.go (L1005-1015)
```go
		steps := capabilities.ResponseMetadata{Metering: []capabilities.MeteringNodeDetail{
			{Peer2PeerID: "xyz", SpendUnit: testUnitA, SpendValue: "0.000007"},
			{Peer2PeerID: "xyz", SpendUnit: testUnitGas, SpendValue: "0.000700000000000000"}, // should convert to 0 credits
		}, CapDON_N: 42}

		require.NoError(t, report.Settle("ref1", steps))

		billingClient.EXPECT().
			SubmitWorkflowReceipt(mock.Anything, mock.MatchedBy(func(report *billing.SubmitWorkflowReceiptRequest) bool {
				return report.CreditsConsumed == "0"
			})).Return(&emptypb.Empty{}, nil).Once()
```
