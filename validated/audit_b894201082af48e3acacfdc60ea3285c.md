## Analysis

The exploit hypothesis is confirmed by the code: `UniversalPayload.GasLimit` is validated only as a well-formed non-negative decimal integer, never bounded against any protocol/block gas cap, and flows unmodified into the explicit `gasLimit` parameter of `DerivedEVMCall`.

**Validation gap.** `UniversalPayload.ValidateBasic` treats `gas_limit` exactly like any other numeric field — it just checks it parses as a non-negative `big.Int`: [1](#0-0) 

**No cap at the call site.** `CallUEAExecutePayload` parses the same string with `SetString` and passes the resulting `*big.Int` straight through as `DerivedEVMCall`'s explicit `gasLimit`, with no comparison against a block/protocol maximum: [2](#0-1) 

**Billing happens after execution, not before.** `DeductGasFeesFromReceipt` computes and burns the fee only from `receipt.GasUsed` (the real, post-execution gas consumption) at `baseFee`, and only enforces `gasUsed <= GasLimit` as a sanity check — it does not gate whether execution was allowed to start: [3](#0-2) 

`ExecutePayloadV2`'s own comment confirms this is a known "free-execution gap" that the `CacheContext` wrapping only closes for *state changes*, not for the *computation itself* — the EVM call is executed first, and fee/balance sufficiency is checked afterward: [4](#0-3) 

Because `MsgExecutePayload` is on the gasless allowlist, the Cosmos-level fee/ante checks are skipped entirely, and "any account may submit" it against an arbitrary `UniversalAccountId`: [5](#0-4) [6](#0-5) 

### Title
Unbounded `UniversalPayload.GasLimit` in `CallUEAExecutePayload` enables unprivileged compute-amplification DoS via gasless `MsgExecutePayload` - (File: `x/uexecutor/keeper/evm.go`)

### Summary
`MsgExecutePayload` is gasless and callable by any account. Its `UniversalPayload.GasLimit` field is only validated as a non-negative decimal string, with no upper bound tied to the chain's block gas limit or any sane protocol maximum. This value is passed verbatim as the explicit `gasLimit` to `DerivedEVMCall`, which executes a real, committed EVM call before the module's own gas-fee accounting (`DeductGasFeesFromReceipt`) checks whether the target UEA can even afford the gas that was used. An attacker can therefore submit a gasless transaction with an astronomically large `GasLimit`, routed through their own (self-controlled, self-signed) UEA to a computation-heavy target contract, causing every node to spend real, potentially unbounded CPU time executing that call during ordinary transaction processing — with the after-the-fact fee deduction simply rolling back state (not undoing the computation cost already incurred) if the UEA cannot pay.

### Finding Description
1. Entry: unprivileged user submits `MsgExecutePayload` (any signer, gasless, no cosmos fee required).
2. `ValidateBasic` on `UniversalPayload` only requires `GasLimit` to parse as a non-negative `uint256`-shaped string — no maximum check exists anywhere in scoped code.
3. `ExecutePayload`/`ExecutePayloadV2` calls `CallUEAExecutePayload`, which does `gasLimit.SetString(universal_payload.GasLimit, 10)` and forwards it directly as the `gasLimit` argument of `DerivedEVMCall` — again with no clamping.
4. `DerivedEVMCall` commits a real EVM call using that gas budget. The Push Chain-side code has no pre-execution balance/affordability check tying the requested `GasLimit` to what the sender (UEA) can actually pay; billing is deferred to `DeductGasFeesFromReceipt`, which runs only after execution and bills strictly by *actual* `GasUsed`, not by the declared `GasLimit`.
5. Consequently, the size of the declared `GasLimit` is unconstrained by protocol logic before the EVM call is committed and executed — the only bound is whatever internal handling the underlying `DerivedEVMCall`/fork implementation applies (not visible in this repository, since it lives in the external `github.com/pushchain/evm` fork).

### Impact Explanation
If the underlying fork does not itself clamp the supplied gas budget to the block gas limit before beginning EVM execution (which the Push-Chain-side code never does), an unprivileged attacker can force a single gasless, freely-submittable transaction to execute with a gas budget many orders of magnitude larger than the chain's intended per-tx/per-block gas limit, targeting a genuinely computation-heavy contract call. Because every honest validator/full node deterministically re-executes this same transaction, this directly amplifies per-tx execution time across all nodes, materially threatening block-processing time/finalization — a non-network-level DoS reachable purely through the scoped `MsgExecutePayload` admission path, matching the in-scope impact category for "denial of service ... not network-level ... reachable without privileged control."

### Likelihood Explanation
High reachability: `MsgExecutePayload` requires no privilege, no fee, and no pre-funded UEA balance to reach `CallUEAExecutePayload` — the fee/affordability check happens only after the EVM call has already run. The missing upper-bound validation is unconditional and present on every call path through `evm.go`.

### Recommendation
Enforce an explicit upper bound on `UniversalPayload.GasLimit` in `ValidateBasic` (or in `CallUEAExecutePayload` before calling `DerivedEVMCall`), clamped to a sane protocol maximum (e.g., the chain's configured block gas limit or a fixed constant well below it), rejecting or clamping any value that exceeds it before the EVM call is issued.

### Proof of Concept
A Go unit test in `x/uexecutor/keeper` asserting:
```go
payload := &types.UniversalPayload{
    GasLimit: "100000000000000000000000000000000000000000000000000000000000000000000000000000", // 10^80
    ...
}
_, err := k.CallUEAExecutePayload(ctx, from, ueaAddr, payload, verificationData)
require.Error(t, err) // currently fails — no such check exists; SetString succeeds and DerivedEVMCall is invoked with the huge value
```
currently fails because no rejection/clamping logic exists in `CallUEAExecutePayload` or `UniversalPayload.ValidateBasic`.

**Caveat/uncertainty:** the actual downstream severity depends on how `DerivedEVMCall` in the external `github.com/pushchain/evm` fork handles an oversized `*big.Int` gas limit (e.g., whether it truncates via `Uint64()`, errors out, or clamps to the block gas limit internally). That fork's source is not part of this repository/index, so I cannot confirm its exact behavior — the finding above is scoped strictly to the confirmed absence of any bound-check in the Push-Chain-side code (`x/uexecutor`) that is supposed to guard this invariant before delegating to the EVM layer.

### Citations

**File:** x/uexecutor/types/universal_payload.go (L41-58)
```go
	// Validate all numeric string fields as uint256
	uintFields := map[string]string{
		"value":                    p.Value,
		"gas_limit":                p.GasLimit,
		"max_fee_per_gas":          p.MaxFeePerGas,
		"max_priority_fee_per_gas": p.MaxPriorityFeePerGas,
		"nonce":                    p.Nonce,
		"deadline":                 p.Deadline,
	}

	for fieldName, value := range uintFields {
		if value != "" {
			bi, ok := new(big.Int).SetString(value, 10)
			if !ok || bi.Sign() < 0 {
				return errors.Wrapf(sdkerrors.ErrInvalidRequest, "%s must be a valid unsigned integer", fieldName)
			}
		}
	}
```

**File:** x/uexecutor/keeper/evm.go (L172-192)
```go
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
```

**File:** x/uexecutor/keeper/fees.go (L97-132)
```go
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-53)
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
```

**File:** app/txpolicy/gasless.go (L14-26)
```go
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
