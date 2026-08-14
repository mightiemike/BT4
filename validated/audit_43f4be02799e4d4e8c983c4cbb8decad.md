### Title
Rounding instead of truncation in gas spend-to-credit conversion allows workflows to under-pay for gas resources - (File: `core/services/workflows/metering/balance_store.go`)

### Summary
`balanceStore.convertToBalance()` converts a resource-dimension amount (e.g. gas spend) into a credit amount to be deducted from a workflow's local balance. For gas spend types it computes `amount.Div(rate).Round(defaultDecimalPrecision)` [1](#0-0) . `decimal.Round()` from `shopspring/decimal` performs standard (round-half-away-from-zero) rounding, not truncation toward zero. This is the same bug class as the Initia `Quo()` vs `QuoTruncate()` issue: using a rounding operation that can round *up* or produce a smaller-than-actual quotient in the payer's favor, instead of an operation that always truncates down, causes the amount charged to systematically diverge from the true cost owed.

### Finding Description
`convertToBalance` is the sole conversion path used by `MinusAs` to determine how many credits to deduct from a workflow's balance for a given resource spend [2](#0-1) . Because `.Round(defaultDecimalPrecision)` rounds to the nearest representable value at 10 decimal places rather than truncating, roughly half of all gas-spend conversions will round the deducted credit amount *down* relative to the true, unrounded cost. `defaultDecimalPrecision` is fixed at 10 [3](#0-2) , so each individual rounding error is bounded to one unit in the 10th decimal place, but it is applied on every single gas-spend deduction across a workflow execution, and workflows can invoke gas-consuming capabilities repeatedly.

This mirrors the report's root cause exactly: a division operation that should truncate toward zero (guaranteeing the payer/consumer never receives more value than they paid for) is instead performed with a rounding function that can resolve in the consumer's favor.

### Impact Explanation
Because credit deduction under-counts on (on average) half of gas conversions, a workflow can consume marginally more gas-denominated resource than the credits it was charged for, repeated across every step and every gas-spend detail processed in `Settle`/`Deduct` paths [4](#0-3) . Over many executions/steps this compounds into value leakage from the billing system — effectively unauthorized (uncharged) resource consumption, which is a data/financial tampering impact against the node operator's billing system rather than a simple self-inconsistency.

### Likelihood Explanation
This code path executes on every metered gas spend conversion for every workflow execution that uses gas-denominated capabilities, so the rounding behavior is triggered with high frequency; the bug requires no attacker action beyond normal workflow execution, and any workflow owner benefits passively from the systematic (if small) under-charging.

### Recommendation
Replace `.Round(defaultDecimalPrecision)` in `convertToBalance` with a truncating rounding mode (e.g. `decimal.Decimal.Truncate(defaultDecimalPrecision)` or an explicit round-down/ceiling mode chosen to always favor the billing system, i.e., never charge less than the true cost) so credit deductions never resolve in the requester's favor [5](#0-4) . Apply the same audit to `convertFromBalance`'s `.Round(0)` usage [6](#0-5)  to ensure balance-to-resource conversions are consistently truncated rather than rounded.

### Proof of Concept
1. Configure a gas conversion rate such that `amount.Div(rate)` yields a value ending in a digit ≥5 at the 11th decimal place, e.g. `amount = 1`, `rate` chosen so the quotient is `X.XXXXXXXXX5...`.
2. Call `balanceStore.MinusAs("GAS.SOME_CHAIN", amount)` [2](#0-1) , which internally calls `convertToBalance` [7](#0-6) .
3. Observe that `.Round(10)` rounds the quotient to the nearest 10th-decimal value; construct inputs where this rounds down relative to the true unrounded quotient, so the credit balance deducted is strictly less than the true cost of the gas consumed.
4. Repeating this deduction over many steps/executions accumulates a measurable discrepancy between gas resources consumed and credits billed.

### Citations

**File:** core/services/workflows/metering/balance_store.go (L49-66)
```go
func (bs *balanceStore) convertToBalance(fromResourceType string, amount decimal.Decimal) (decimal.Decimal, error) {
	rate, ok := bs.conversions[fromResourceType]
	if !ok {
		return amount, ErrResourceTypeNotFound
	}

	// Special case for gas as gas token conversions are provided in amount per credit.
	// Other rates are provided as the inverse.
	if isGasSpendType(fromResourceType) {
		if rate.IsZero() {
			return decimal.Zero, nil
		}

		return amount.Div(rate).Round(defaultDecimalPrecision), nil
	}

	return amount.Mul(rate), nil
}
```

**File:** core/services/workflows/metering/balance_store.go (L86-88)
```go
	if isGasSpendType(toResourceType) {
		return amount.Mul(rate).Round(0), nil
	}
```

**File:** core/services/workflows/metering/balance_store.go (L148-169)
```go
// MinusAs lowers the current credit balance based on an amount of resource dimensions.
func (bs *balanceStore) MinusAs(resourceType string, amount decimal.Decimal) error {
	bs.mu.Lock()
	defer bs.mu.Unlock()

	if amount.LessThan(decimal.Zero) {
		return ErrInvalidAmount
	}

	balToMinus, err := bs.convertToBalance(resourceType, amount)
	if err != nil {
		return err
	}

	if balToMinus.GreaterThan(bs.balance) {
		return ErrInsufficientBalance
	}

	bs.balance = bs.balance.Sub(balToMinus)

	return nil
}
```

**File:** core/services/workflows/metering/metering.go (L31-36)
```go
const (
	RatiosKey = "spendRatios"
	// the default decimal precision is a fixed number defined in the billing service. if this gets changed
	// in the billing service project, the value here needs to change.
	defaultDecimalPrecision = 10 // one thousandth of a dollar

```

**File:** core/services/workflows/metering/metering.go (L450-465)
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

			deciVals = append(deciVals, value)

			if isGasSpendType(unit) && len(deciVals) > 1 {
				r.switchToMeteringMode(fmt.Errorf("multiple executions for single execution unit [%s]: %w", unit, err))
			}
```
