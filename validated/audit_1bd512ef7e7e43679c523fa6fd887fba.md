I have enough context to evaluate the analog. Let me do a final targeted check on `msg_execute_payload.go` around `ExecutePayload` (v1, non-cached path) since it deducts gas fees on the raw context instead of a cache, which is the closest structural analog to a guard that behaves differently than the "other" variant (V2), similar to how `CEtherDelegator` inherited a check that applied to other markets but not itself.## Analysis

The external report's bug class is: two structurally parallel code paths implement the same "protected" operation, but one path drops a guard/atomicity property that the other correctly enforces because it inherited assumptions that didn't carry over. In `CEtherDelegator`, the fallback's `msg.value` guard was copied from ERC20-market delegators without accounting for CEther's need to receive value.

Push Chain has a direct structural analog in the UEA payload-execution flow, and the codebase's own comments confirm the invariant is deliberately enforced in one sibling but missing in another.

### The two sibling paths

- `x/uexecutor/keeper/execute_payload.go` (`ExecutePayloadV2`, used by the inbound-vote-driven `ExecuteInboundFundsAndPayload`) wraps the EVM call and the gas-fee deduction in an explicit `sdkCtx.CacheContext()`: [1](#0-0) 
The comment is explicit about why: *"If fee deduction fails, the EVM state changes from CallUEAExecutePayload are discarded — closes the free-execution gap when the UEA has no native UPC to cover gas."*

- `x/uexecutor/keeper/msg_execute_payload.go` (`ExecutePayload`, the handler invoked directly by the user-facing, gasless `MsgExecutePayload` message) performs the **same** sequence — call `CallUEAExecutePayload`, then `DeductGasFeesFromReceipt` — but does **not** use a `CacheContext`. It executes the EVM call directly against `sdkCtx`: [2](#0-1) 

This is called directly from the message server for the user-reachable, gasless `MsgExecutePayload`: [3](#0-2) 

`MsgExecutePayload` is in the gasless whitelist and can be submitted by **any** unprivileged signer on behalf of any `UniversalAccountId` (contract-only binding — signer need not own the UEA): [4](#0-3) [5](#0-4) 

### Why this matters

`CallUEAExecutePayload` issues a `commit=true` `DerivedEVMCall`, which writes real EVM state (storage, balances, logs) as part of the transaction: [6](#0-5) 

Elsewhere in the same module, the team already identified and fixed this exact class of bug for the CEA/inbound path (tagged `F-2026-16738` in comments and tests), explicitly wrapping the call + fee-deduction pair in a `CacheContext` "so they commit/revert together," specifically to prevent a payload from executing (mutating EVM state) while the corresponding gas fee is never actually collected: [7](#0-6) [8](#0-7) 

The `msg_execute_payload.go` path — the one reachable directly by an unprivileged, gasless `MsgExecutePayload` from any account — never received the equivalent fix. It calls the EVM directly on `sdkCtx` and only detects the fee failure *after* the EVM state has already been written, then returns an error hoping BaseApp's outer message-branch rollback undoes it. Given that the sibling code path had to add an *explicit* `CacheContext` to close this exact gap, relying on implicit outer rollback here is the same category of mistake as `CEtherDelegator` inheriting a fallback guard that doesn't hold for its own context — an assumption ("errors here roll back everything") that the team's own parallel fix proves does not reliably hold for `DerivedEVMCall`'s EVM state commits.

### Impact

If `DeductGasFeesFromReceipt` fails after `CallUEAExecutePayload` has already committed EVM state (e.g., a UEA drained of native `upc` right before the fee deduction step, or a payload whose own execution spends/moves the UEA's `upc` balance down to below the required gas cost), the attacker gets the payload's state-changing effects (arbitrary calls out of the UEA, PRC20/native transfers, outbound-creating calls) executed for free, with `x/uexecutor` unable to actually collect the gas cost — corrupting the gas-fee/UEA-fund accounting invariant that `DeductGasFeesFromReceipt`/`DeductAndBurnFees` is supposed to enforce.

## Uncertainty

I could not verify from the indexed code whether Cosmos SDK's/`cosmos-evm`'s outer `runMsgs` cache-multistore branch actually *does* discard the EVM keeper's `StateDB.Commit()` writes when the message handler subsequently returns a non-nil error (this depends on internals of the `cosmos-evm` fork's `DerivedEVMCall`/state commit implementation, which isn't in the indexed snippets). The team's own `CacheContext` fix and its accompanying comment ("closes the free-execution gap") for the sibling path is the strongest evidence that outer rollback is *not* sufficient — otherwise that explicit fix would have been unnecessary. Given index size limits, I recommend starting a Devin session to trace `DerivedEVMCall`'s commit semantics in the `pushchain/evm` fork and confirm whether `msg_execute_payload.go:ExecutePayload` genuinely lacks the atomicity guarantee, before treating this as a confirmed, exploitable finding.

### Citations

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

**File:** app/txpolicy/gasless.go (L12-26)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```

**File:** x/uexecutor/README.md (L211-218)
```markdown
### Authorization model for `MsgExecutePayload` (contract-only binding)

`MsgExecutePayload` follows a **contract-only binding** authorization model. The Cosmos signer of the message and the owner of the target Universal Account are intentionally distinct roles:

- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.

**The chain module deliberately does not enforce `Signer == EVM(Owner)`.** If it did, third-party delivery of owner-signed payloads would be impossible — every owner would have to submit their own Cosmos transactions even though the chain charges them no Cosmos fee for doing so, defeating the cross-chain UX promise of letting an external account act on Push Chain through delivered payloads.
```

**File:** x/uexecutor/keeper/evm.go (L155-193)
```go
// CallUEAExecutePayload executes a universal payload through UEA
func (k Keeper) CallUEAExecutePayload(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	universal_payload *types.UniversalPayload,
	verificationData []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiUniversalPayload, err := types.NewAbiUniversalPayload(universal_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
}
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
