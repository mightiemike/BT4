I identified a concrete analog: an unhandled panic due to negative-index array access in `medianSpend`, reachable from capability response data processed during workflow metering settlement.

### Title
Unhandled panic (index out of range) in `medianSpend` when all node-reported spend values are unparseable - (File: `core/services/workflows/metering/metering.go`)

### Summary
`medianSpend` computes a median over a slice of `decimal.Decimal` values without checking for an empty input, causing a negative-index slice access and a runtime panic when the input slice is empty.

### Finding Description
`medianSpend` sorts the `spends` slice and then computes the median: [1](#0-0) 

When `len(spends) == 0`, `len(spends)%2 == 0`, so execution falls into the even branch: `spends[len(spends)/2-1]` evaluates to `spends[-1]`, which is an invalid slice index in Go and panics with `index out of range [-1]`.

This function is called from `Report.Settle`, which builds `deciVals` from capability node responses (`metadata.Metering`) grouped by spend unit: [2](#0-1) 

Crucially, for every `spendDetails` entry, if `decimal.NewFromString(detail.SpendValue)` fails to parse, the loop does `continue`, discarding that entry from `deciVals` without any fallback: [3](#0-2) 

If *every* node detail for a given spend unit reports a non-numeric/unparseable `SpendValue` string, `deciVals` ends up empty, and the subsequent call `medianSpend(deciVals)` panics: [4](#0-3) 

### Impact Explanation
`capabilities.ResponseMetadata.Metering` (containing `SpendValue` strings) is populated from capability response data during workflow execution — data that a capability (e.g., a custom/compute or external capability handler) controls. A capability that reports a garbage/non-numeric `SpendValue` for all of its node details for a given spend unit causes `Report.Settle` to panic. Since `Settle` runs inside the workflow engine's per-step execution/accounting path, an unhandled panic here can crash the goroutine processing a workflow execution, denying service for metering/billing accounting of that workflow run (and potentially the engine if not recovered upstream).

### Likelihood Explanation
Requires a capability to consistently return a non-numeric `SpendValue` for one resource/spend unit across all of its `MeteringNodeDetail` entries in a single `Settle` call. This is plausible for a misbehaving, buggy, or malicious capability implementation that supplies attacker/implementer-controlled `SpendValue` strings, without any special node/OCR compromise being required — it's a data-shape issue in externally-supplied capability response fields, not a byzantine-consensus assumption violation.

### Recommendation
Add an explicit empty-slice guard at the top of `medianSpend` (mirroring the `len(values) == 0` checks already present in `pipeline.MedianTask.Run`, see `core/services/pipeline/task.median.go` lines 83-87) and return a zero value or a descriptive error/skip when there are no valid parsed spend values for a resource unit in `Settle`, rather than falling through to compute a median on an empty slice.

### Proof of Concept
1. Construct `capabilities.ResponseMetadata.Metering` with one or more `MeteringNodeDetail` entries for a given `SpendUnit`, where `SpendValue` is a non-numeric string (e.g., `"not-a-number"`) for all entries of that unit.
2. Call `Report.Settle(ref, metadata)`.
3. In the aggregation loop, `decimal.NewFromString(detail.SpendValue)` fails for every entry of that unit, so `deciVals` remains empty (`len(deciVals) == 0`).
4. `medianSpend(deciVals)` is invoked at line 476, hits the even-length branch, and panics on `spends[-1]`.

### Citations

**File:** core/services/workflows/metering/metering.go (L434-466)
```go
	// Aggregate node responses to a single number
	for unit, spendDetails := range resourceSpends {
		aggregated := AggregatedStepDetail{
			SpendUnit:  unit,
			SpendValue: decimal.Zero,
		}

		deciVals := []decimal.Decimal{}
		for idx, detail := range spendDetails {
			value, err := decimal.NewFromString(detail.SpendValue)
			if err != nil {
				r.lggr.Info(fmt.Sprintf("failed to get spend value from %s: %s", detail.SpendValue, err))
				// throw out invalid values for local balance settlement. they will still be included in metering report.
				continue
			}

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
		}
```

**File:** core/services/workflows/metering/metering.go (L476-476)
```go
		aggregated.SpendValue = medianSpend(deciVals)
```

**File:** core/services/workflows/metering/metering.go (L849-859)
```go
func medianSpend(spends []decimal.Decimal) decimal.Decimal {
	sort.Slice(spends, func(i, j int) bool {
		return spends[j].GreaterThan(spends[i])
	})

	if len(spends)%2 > 0 {
		return spends[len(spends)/2]
	}

	return spends[len(spends)/2-1].Add(spends[len(spends)/2]).Div(decimal.NewFromInt(2))
}
```
