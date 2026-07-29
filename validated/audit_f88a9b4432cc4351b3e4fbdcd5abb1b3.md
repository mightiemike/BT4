## Title
Free, Repeatable Gas-Griefing DoS via Gasless `MsgExecutePayload` with Unbounded User-Controlled `gas_limit` and Fee-Failure Full Rollback - (File: `x/uexecutor/keeper/execute_payload.go`, `x/uexecutor/keeper/fees.go`, `x/uexecutor/types/universal_payload.go`, `app/txpolicy/gasless.go`)

### Summary
`MsgExecutePayload` is a gasless Cosmos message (whitelisted in `app/txpolicy/gasless.go`) that lets any signer submit a `UniversalPayload` whose `GasLimit` field is fully attacker-controlled and unbounded, then routes it through `DerivedEVMCall` for real, committed EVM execution before any gas-fee/balance check happens. Because the actual gas cost is only deducted *after* execution, and a failed deduction discards the entire state change via `CacheContext`, an unprivileged UEA owner can repeatedly submit maximally expensive payloads from an underfunded UEA at zero Cosmos-level cost, forcing validators to perform real, unbounded EVM computation for free — the same "gas griefing" bug class as the Hinkal report, but here the griefed party is the validator/proposer set rather than a relayer, and the attack costs the attacker nothing because the message type is gasless.

### Finding Description
`MsgExecutePayload.UniversalPayload.GasLimit` is validated only as a well-formed, non-negative uint256 in `UniversalPayload.ValidateBasic()`: [1](#0-0) 

There is no upper bound check anywhere in the message validation path (`MsgExecutePayload.ValidateBasic`) or in the keeper handling path (`ExecutePayloadV2`) that limits how large a caller may set this value: [2](#0-1) 

The gas limit flows unmodified into a real, committed `DerivedEVMCall`: [3](#0-2) 

`ExecutePayloadV2` performs the EVM call *first*, inside a `CacheContext`, and only afterward attempts `DeductGasFeesFromReceipt`. If fee deduction fails (e.g., insufficient balance on the target UEA), the entire cache — including any partial fee collected — is discarded and never written: [4](#0-3) 

`DeductGasFeesFromReceipt` computes cost as `baseFee * gasUsed` and simply returns an error (causing the caller to roll back) when `DeductAndBurnFees` fails due to insufficient balance: [5](#0-4) 

Crucially, `MsgExecutePayload` is on the gasless whitelist, meaning it bypasses the Cosmos-level `MinGasPriceDecorator` and `DeductFeeDecorator` entirely — the submitter pays no tx fee and is not subject to minimum gas price checks: [6](#0-5) [7](#0-6) [8](#0-7) 

Putting this together: an unprivileged party who owns (or controls the private key of) a UEA with zero or near-zero balance can:
1. Sign a `UniversalPayload` calling an expensive, attacker-deployed contract (e.g., a busy-loop) with a very large `gas_limit`.
2. Submit `MsgExecutePayload` — free of any Cosmos tx fee and free of min-gas-price enforcement because the message is gasless.
3. The node executes the full EVM call (consuming real, unbounded gas/CPU up to `gas_limit`, capped only by the EVM/block gas limit) before ever checking whether the UEA can pay.
4. Because the UEA balance is zero, `DeductGasFeesFromReceipt` fails, the entire `CacheContext` — including the executed EVM state and any fee attempt — is discarded, and the transaction is otherwise reported as failed with no on-chain cost to the attacker.
5. Step 1–4 can be repeated indefinitely, at zero marginal cost to the attacker, each time forcing the node to burn real computational resources for no compensation.

This mirrors the original report's core issue — a party that controls arbitrary, high-gas-consuming call targets can force the fee-collecting party (there: relayer; here: validator/proposer, since `MsgExecutePayload` is gasless) to expend real gas without receiving commensurate payment — but is worse here because the attack is entirely gasless at the outer layer (no fee is even nominally owed by the submitter) and the fee-check-after-execution design guarantees a full, free rollback path whenever the payer is underfunded.

### Impact Explanation
This is a non-network-level, unprivileged-reachable denial-of-service vector: an ordinary user (owning any UEA, which requires no special permission to create) can repeatedly force validators to execute large, attacker-chosen EVM computations for zero cost, degrading block production throughput/latency for the whole network. This falls within the allowed impact scope ("denial of service ... when it is not network-level and is reachable without privileged control").

### Likelihood Explanation
High. No privileged role, TSS/UV bonding, or governance action is required — only ownership of a UEA (or an EVM/SVM keypair used to derive one) and the ability to sign an arbitrary payload, which any user can do. The gasless whitelist and post-execution fee check make the attack essentially free and trivially repeatable.

### Recommendation
- Enforce a protocol-level maximum `gas_limit` for `UniversalPayload` in `ValidateBasic` and/or in the keeper before issuing `DerivedEVMCall`.
- Verify the target UEA/owner's balance is sufficient to cover the worst-case gas cost (`gas_limit * maxFeePerGas` or similar) *before* committing the EVM call, not only after execution.
- Consider removing `MsgExecutePayload` from the unconditional gasless whitelist, or applying a per-signer/per-UEA rate limit or minimum bonded stake requirement for gasless submissions to prevent unlimited free resubmission.

### Proof of Concept
1. Deploy (or reuse) a contract with an expensive/looping function on the Push Chain EVM.
2. Create a fresh UEA with zero `upc` balance.
3. Craft and sign a `UniversalPayload` targeting the expensive function with `gas_limit` set near the block gas limit.
4. Submit `MsgExecutePayload` (gasless — bypasses `MinGasPriceDecorator` and `DeductFeeDecorator`).
5. Observe: the EVM call executes fully (consuming real gas/CPU), `DeductGasFeesFromReceipt` fails due to zero balance, the whole `CacheContext` is discarded, and the transaction fails at zero cost to the submitter.
6. Repeat step 4 indefinitely to sustain the griefing load.

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

**File:** x/uexecutor/types/msg_execute_payload.go (L48-84)
```go
// ValidateBasic does a sanity check on the provided data.
func (msg *MsgExecutePayload) ValidateBasic() error {
	// Validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	// Validate universalAccountId
	if msg.UniversalAccountId == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "universal account cannot be nil")
	}

	// Validate universal payload
	if msg.UniversalPayload == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "universal payload cannot be nil")
	}

	// Validate verificationData
	if len(msg.VerificationData) == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "verificationData cannot be empty")
	}
	if _, err := hex.DecodeString(strings.TrimPrefix(msg.VerificationData, "0x")); err != nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "invalid verificationData hex")
	}

	// Validate universalAccountId structure
	if err := msg.UniversalAccountId.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid universalAccountId")
	}

	// Validate universal payload structure
	if err := msg.UniversalPayload.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid universal payload")
	}

	return nil
}
```

**File:** x/uexecutor/keeper/evm.go (L156-193)
```go
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

**File:** x/uexecutor/keeper/fees.go (L93-140)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
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

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```

**File:** app/README.md (L161-170)
```markdown
**The gasless whitelist** (`app/txpolicy/gasless.go`) — only these message types qualify:

```
/uexecutor.v1.MsgExecutePayload
/uexecutor.v1.MsgVoteInbound
/uexecutor.v1.MsgVoteOutbound
/uexecutor.v1.MsgVoteChainMeta
/utss.v1.MsgVoteTssKeyProcess
/utss.v1.MsgVoteFundMigration
```
```

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```
