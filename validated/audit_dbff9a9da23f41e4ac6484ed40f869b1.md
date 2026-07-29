## Analysis

The Perennial bug involves using a "latest" oracle value (which can be stale, zero, or otherwise different from the value that was valid when the action was decided) in a strict comparison during a critical execution path, causing the action to revert in edge cases that Sherlock accepted as low-probability but real.

The Push Chain analog lives in the universal payload gas-fee accounting path, where a **live, validator-local, block-varying `baseFee`** is compared against a **payload-fixed `MaxFeePerGas`** that was locked in at signing time (on the source chain, well before on-chain finalization), and a mismatch causes the entire transaction — including an already-successful EVM payload execution — to revert.

### Title
Reliance on live EIP-1559 `baseFee` instead of the fee committed at payload-signing time can revert an already-executed, legitimate universal payload - (File: `x/uexecutor/keeper/fees.go`, `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
`DeductGasFeesFromReceipt` fetches the *current* chain `baseFee` via `k.feemarketKeeper.GetBaseFee(sdkCtx)` and feeds it into `CalculateGasCost`, which hard-fails if the user's `MaxFeePerGas` (fixed inside the signed `UniversalPayload`, analogous to a "locked-in" price) is below the live base fee: [1](#0-0) . This mirrors the Perennial pattern of validating against a volatile "latest" value instead of the value that was valid/intended at decision time.

### Finding Description
`ExecutePayload` (direct message path) executes the UEA payload first, then deducts gas fees, and explicitly documents that a fee-deduction failure rolls back the *entire* Cosmos transaction, including the EVM execution that just succeeded: [2](#0-1) 

`DeductGasFeesFromReceipt` performs the live-baseFee lookup and comparison: [3](#0-2) 

`CalculateGasCost` treats any `MaxFeePerGas < currentBaseFee` as a hard error rather than clamping/falling back to the effective/valid price: [1](#0-0) 

`MaxFeePerGas` is part of the `UniversalPayload` that is signed/committed by the user (via their inbound intent or CAIP-10-signed payload) well before it reaches on-chain finalization on Push Chain — inbound observation requires 2/3+ Universal Validator quorum, which can span multiple blocks. Push Chain's `feemarket` `baseFee` (EIP-1559-style) adjusts per block based on ordinary network gas usage. An unprivileged party can submit ordinary high-gas transactions in the blocks between payload-intent creation and finalization to push the live `baseFee` above the victim's locked `MaxFeePerGas`, deterministically forcing `CalculateGasCost` to error and, per the documented behavior, rolling back the entire transaction (including the just-executed UEA call) even though the payload itself was otherwise valid and correctly authorized.

The same live-vs-committed mismatch also exists in the inbound-flow variants (`ExecuteInboundGasAndPayload`, `ExecuteInboundFundsAndPayload`, `ExecutePayloadV2`), where the failure is contained to a `CacheContext` rollback rather than the whole tx, but still results in the recipient's legitimate, already-executed payload being discarded and recorded as `FAILED`: [4](#0-3) .

### Impact Explanation
This is a griefing/availability issue reachable by any unprivileged user submitting ordinary transactions to raise the live `baseFee`: a targeted victim's otherwise-valid, correctly-signed `UniversalPayload` execution can be made to revert or be rolled back solely because the network's live fee state moved between payload commitment and execution, not because of any fault of the payload or its authorization. In the direct-message path (`msg_execute_payload.go`), the entire transaction (not just fee accounting) reverts, discarding otherwise-successful EVM execution. This is a DoS on legitimate execution flows reachable without any privileged control, matching the "reachable without privileged control" DoS carve-in in scope, though it does not directly cause fund loss.

### Likelihood Explanation
Low-to-moderate: exploitation requires an attacker to sustain elevated gas usage across the (typically short, but multi-block due to ballot quorum) window between payload signing and finalization to keep `baseFee` above the victim's fixed `MaxFeePerGas`. This is analogous to the Sherlock-acknowledged Perennial issue: a real but narrow-probability interaction between a volatile "latest" value and a value fixed earlier in the flow, rather than a certain, cheap, or high-value exploit.

### Recommendation
Use a fee model tolerant of `baseFee` drift between signing and execution — e.g., cap the effective gas price at `MaxFeePerGas` (clamping instead of erroring) similar to standard EIP-1559 semantics (`effectiveGasPrice = min(maxFeePerGas, baseFee + tip)`), or capture/commit the `baseFee` at the point the inbound/payload intent is first observed rather than at final execution time, so a temporary fee spike does not retroactively invalidate an already-executed, correctly-authorized payload.

### Proof of Concept
1. User signs a `UniversalPayload` with `MaxFeePerGas = X` and submits the inbound transaction/intent.
2. Between intent creation and inbound-ballot finalization (multi-block, 2/3+ UV quorum), an unprivileged party submits ordinary high-gas-usage transactions on Push Chain to raise `feemarketKeeper`'s `baseFee` above `X`.
3. When the honest Universal Validators finalize the inbound and `ExecutePayloadV2`/`ExecutePayload` runs, `k.feemarketKeeper.GetBaseFee(sdkCtx)` returns the now-elevated baseFee [5](#0-4) .
4. `CalculateGasCost` returns `"maxFeePerGas (%s) cannot be less than baseFee (%s)"` [1](#0-0) , causing `DeductGasFeesFromReceipt` to fail.
5. In the direct-message path, this failure propagates up and reverts the whole transaction including the already-run EVM payload [6](#0-5) ; in inbound flows, the `CacheContext` write is discarded, marking the callPcTx `FAILED` despite a successful underlying EVM call [7](#0-6) .

### Citations

**File:** x/uexecutor/keeper/fees.go (L62-65)
```go
	// Step 1: Validate maxFeePerGas >= baseFee
	if maxFeePerGas.Cmp(baseFeeBig) < 0 {
		return nil, fmt.Errorf("maxFeePerGas (%s) cannot be less than baseFee (%s)", maxFeePerGas, baseFeeBig)
	}
```

**File:** x/uexecutor/keeper/fees.go (L116-124)
```go
	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-97)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		return execErr
	}
```

**File:** x/uexecutor/keeper/execute_payload.go (L39-53)
```go
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```
