Confirmed: `msgServer.ExecutePayload` at [1](#0-0)  is the live message handler for `MsgExecutePayload`, and it calls straight into `Keeper.ExecutePayload`, which lacks the `CacheContext` protection that the newer `ExecutePayloadV2` path has.

### Title
Unrolled-back EVM state on gas-fee-deduction failure in `MsgExecutePayload` handler (`x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
The Perennial bug is a case where a value (the liquidation fee) is computed from account state *before* an intervening state-mutating call (`product.closeAll`), and the value is then used post-mutation in a way that breaks an invariant (an underflow that blocks liquidation entirely). The Push Chain analog is structurally similar in spirit: `Keeper.ExecutePayload` executes the user's UEA payload first (which can mutate the UEA's native `upc` balance, e.g. by transferring it out during payload execution) and only afterward attempts to bill gas fees against that same balance — but unlike the sibling `ExecutePayloadV2` path, this legacy handler does the EVM call directly against `sdkCtx` instead of a `CacheContext`.

### Finding Description
`Keeper.ExecutePayload` (invoked by `msgServer.ExecutePayload`, the handler for `MsgExecutePayload`) performs:
1. `CallUEAExecutePayload(sdkCtx, ...)` — a `DerivedEVMCall` with `commit=true` executed directly on `sdkCtx`, not a cache-wrapped context.
2. `DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload)` — attempts to debit `upc` from the UEA account to pay for the gas that was just consumed. [2](#0-1) 

Compare this to the newer `ExecutePayloadV2` (used by the inbound funds/CEA/gas-and-payload flows), which explicitly wraps both the EVM call and the fee deduction in `sdkCtx.CacheContext()` and only calls `writeCache()` if fee deduction succeeds, with an explicit comment that this "closes the free-execution gap when the UEA has no native UPC to cover gas": [3](#0-2) 

A dedicated regression test elsewhere in the repo (`inbound_cea_smart_contract_test.go`, tagged `F-2026-16738`) documents that this exact gap was previously fixed for the smart-contract/CEA execution path (`execute_inbound_funds_and_payload.go`) by adding a `CacheContext`: [4](#0-3) [5](#0-4) 

The `MsgExecutePayload` handler (`x/uexecutor/keeper/msg_execute_payload.go`), however, still lacks this protection. If `CallUEAExecutePayload`'s payload logic changes the UEA's native `upc` balance during execution (e.g., the payload itself transfers `upc` out of the UEA, or the UEA has insufficient balance to cover the gas that the just-executed call consumed), `DeductGasFeesFromReceipt` can fail with an error. Because Step 3's EVM call was committed directly to `sdkCtx` (not a discardable cache), the EVM side-effects of `CallUEAExecutePayload` are **not automatically rolled back** by returning an error from `ExecutePayload`, whereas at the higher Cosmos SDK message-processing layer, error returns from a `Msg` handler normally cause the branched store for that message to be discarded — meaning whether the EVM state persists depends on how `DerivedEVMCall`'s `commit=true` interacts with the SDK's own message-level branching, which the `ExecutePayloadV2`/CacheContext fix implies is *not* fully covered by that outer branching alone (otherwise the fix would have been unnecessary).

### Impact Explanation
If the outer SDK message-level rollback does not fully undo `DerivedEVMCall`'s committed EVM-state writes (the exact scenario the `CacheContext` fix in `ExecutePayloadV2` was introduced to close), a user could execute a UEA payload that consumes real EVM state/gas for free by structuring the payload so that gas-fee deduction subsequently fails (e.g., draining the UEA's `upc` balance as part of the payload, or invoking `MsgExecutePayload` against a UEA that never held enough native `upc`). This mirrors the "protocol invariant violated by a computed value that goes stale mid-flow" root cause pattern in the source report — funds/computation obtained without paying, an unauthorized free execution of module-originated EVM logic.

### Likelihood Explanation
Likelihood depends entirely on how `DerivedEVMCall`(`commit=true`) participates in SDK message-level store branching for the *un-cached* direct-`sdkCtx` call path. I could not fully verify from the available Go-EVM-fork source whether the message-level branch alone provides sufficient atomicity, or whether the fix applied in `ExecutePayloadV2`/`execute_inbound_funds_and_payload.go` was specifically necessary because such branching is insufficient. The existence of the `F-2026-16738` fix and its explicit rationale ("closes the free-execution gap when the UEA has no native UPC to cover gas") strongly suggests the un-cached call site has this exact gap, but I do not have direct proof this legacy path (`msg_execute_payload.go`) was included in that remediation — it appears to have been missed.

### Recommendation
Apply the same `CacheContext` pattern used in `execute_payload.go`'s `ExecutePayloadV2` to `x/uexecutor/keeper/msg_execute_payload.go`'s `ExecutePayload`: wrap `CallUEAExecutePayload` and `DeductGasFeesFromReceipt` in a single `sdkCtx.CacheContext()`, only calling `writeCache()` when both succeed, so that a fee-deduction failure discards the EVM execution rather than leaving it committed.

### Proof of Concept
Not independently reproducible from static analysis alone — confirming exploitability requires running a local devnet: submit `MsgExecutePayload` for a UEA that (a) starts with just enough `upc` to appear fundable, and (b) whose payload transfers/burns the UEA's `upc` balance during execution (or simply has zero `upc`), then observe whether `DeductGasFeesFromReceipt`'s failure actually rolls back the EVM state mutated by `CallUEAExecutePayload`, or whether the payload's effects persist despite the returned error. This is left as a task for a background agent with node/devnet access, since it cannot be settled from source inspection alone.

### Citations

**File:** x/uexecutor/keeper/msg_server.go (L42-55)
```go
// ExecutePayload handles universal payload execution on the UEA.
func (ms msgServer) ExecutePayload(ctx context.Context, msg *types.MsgExecutePayload) (*types.MsgExecutePayloadResponse, error) {
	_, evmFromAddress, err := utils.GetAddressPair(msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to parse signer address")
	}

	err = ms.k.ExecutePayload(ctx, evmFromAddress, msg.UniversalAccountId, msg.UniversalPayload, msg.VerificationData)
	if err != nil {
		return nil, err
	}

	return &types.MsgExecutePayloadResponse{}, nil
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

**File:** x/uexecutor/keeper/execute_payload.go (L35-56)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
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

	// Both succeeded — commit EVM state and fee deduction together.
	writeCache()
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L354-360)
```go
	// F-2026-16738: when DeductGasFeesFromReceipt fails after a successful
	// CallExecuteUniversalTx, the EVM call + fee deduction now run inside a
	// CacheContext that is discarded on fee failure. The deposit (which
	// happens before this scope) stays committed; the executeUniversalTx
	// state changes are rolled back so the recipient cannot consume gas
	// without paying for it.
	t.Run("fee deduction failure rolls back executeUniversalTx, keeps deposit", func(t *testing.T) {
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L233-255)
```go
				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
```
