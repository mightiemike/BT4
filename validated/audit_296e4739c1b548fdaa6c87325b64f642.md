### Title
Attacker-controlled `RevertInstructions.FundRecipient` can permanently strand bridged funds in `ABORTED` outbounds with no recovery path - (File: x/uexecutor/keeper/outbound.go)

### Summary
The report's bug class — proposal-registration tokens that cannot be refunded end up permanently and undocumented-ly stuck in the contract — maps to Push Chain's outbound-revert flow. When an outbound fails on the destination chain and the module tries to mint bridged funds back on Push Chain via `CallPRC20Deposit`, a failure there marks the outbound `ABORTED` "for manual intervention" and simply stops, exactly like `PolicyProposals.destruct()` locking non-refunded tokens with no documented or automatic path to recover them.

### Finding Description
`handleFailedOutbound` in [1](#0-0)  re-mints bridged PRC20 tokens to a `recipient` that is derived from `outbound.RevertInstructions.FundRecipient` (falling back to `outbound.Sender`) when an outbound (FUNDS / GAS_AND_PAYLOAD / FUNDS_AND_PAYLOAD) is observed as failed on the destination chain. If the `CallPRC20Deposit` EVM call fails, the code path is: [2](#0-1) 

which calls `AbortOutbound`: [3](#0-2) 

`AbortOutbound` only sets `Status_ABORTED` and an `AbortReason`, removes the entry from `PendingOutbounds`, and emits a monitoring event — there is no automatic retry, no fallback recipient, and (based on the codebase search) no other keeper function that ever resumes or re-mints funds for an `ABORTED` outbound. The comment says "manual intervention," but no privileged recovery flow was found in the module.

`RevertInstructions.FundRecipient` originates from the original inbound/outbound instructions attached by the user/relayer for the cross-chain transaction (see `buildRevertOutbound`, [4](#0-3) ), i.e., it is attacker/user-controllable data, not something only validators or admins set. Because `depositPRC20Token` on the `UniversalCore` handler contract is invoked with this recipient address ( [5](#0-4) ), any condition that makes that EVM call revert (e.g., an EOA/contract chosen as `FundRecipient` that cannot receive the token, a malformed/blacklisted address for a token with a transfer restriction, or gas/limit conditions on the module-originated call) permanently traps the tokens in the outbound entry with `Status_ABORTED`.

### Impact Explanation
This is a "permanent freezing of user funds" scenario, directly analogous to the ECO report: the underlying bridged asset value has already been burned/removed from the sender's side (the outbound was created to represent it leaving Push Chain), and when the destination-chain leg fails, the module's only recovery mechanism (re-mint back to the user) can be made to permanently fail with no automatic or admin-triggered on-chain remediation found in the code. The user's funds become unrecoverable through any code path in this module, and — just like the ECO finding — the module offers no documentation of what happens to them or who, if anyone, can eventually release them.

### Likelihood Explanation
The trigger (choosing/controlling the value used as `FundRecipient` in `RevertInstructions`) is reachable through ordinary inbound submission — an unprivileged user supplies this data as part of their own cross-chain transaction. Causing `CallPRC20Deposit` to revert deterministically for a chosen recipient is plausible depending on the concrete PRC20/UniversalCore contract implementation (not present in this Go repository — the contract logic lives outside `x/`), so I cannot confirm from the indexed Go code alone whether the deposit call can be forced to revert by an ordinary EOA/contract choice as opposed to only failing under abnormal conditions (e.g., EVM out-of-gas from module-side gas accounting). This uncertainty is the main gap: the reachable-and-deterministic nature of the revert depends on Solidity contract code that is not part of this repository's indexed content.

### Recommendation
- Do not let a single failed re-mint permanently abort recovery. Add a retry mechanism or a safe fallback recipient (e.g., the original `Sender`, or a rescue/dead-letter account) when the configured `FundRecipient` causes the deposit call to fail.
- Document explicitly what `ABORTED` means: whether tokens are considered burned, who (if anyone) can act on an `ABORTED` outbound, and add a governance/admin message (analogous to `MsgRevertStuckInbound`) that can redirect or reattempt the mint-back to a different recipient.
- Consider validating/sanitizing `RevertInstructions.FundRecipient` before creating the outbound so that inputs that are known to be unable to receive the token type are rejected up front rather than causing terminal `ABORTED` state after funds are already committed to the revert flow.

### Proof of Concept
Not independently reproducible from the indexed Go code alone, since the actual PRC20/`UniversalCore.depositPRC20Token` Solidity implementation determining whether a chosen `FundRecipient` can force a revert is outside this repository's indexed contents. Conceptually:
1. Submit an inbound with `TxType_FUNDS`/`FUNDS_AND_PAYLOAD` and `RevertInstructions.FundRecipient` set to an address/contract chosen to make PRC20 receipt fail (e.g., a contract with no fallback, or one that reverts on token receipt if the PRC20 implements transfer hooks).
2. Have the corresponding outbound observed as failed on the destination chain, triggering `handleFailedOutbound`.
3. `CallPRC20Deposit` fails against the crafted recipient; `AbortOutbound` is invoked, setting `Status_ABORTED` with no further automated recovery, per [2](#0-1) .

### Citations

**File:** x/uexecutor/keeper/outbound.go (L45-69)
```go
// AbortOutbound marks an outbound as ABORTED with a reason.
// This signals that automatic processing has failed and manual intervention is needed.
func (k Keeper) AbortOutbound(ctx context.Context, utxId string, outbound types.OutboundTx, reason string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	outbound.OutboundStatus = types.Status_ABORTED
	outbound.AbortReason = reason

	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Defensively remove from pending index (may already be removed by caller)
	_ = k.PendingOutbounds.Remove(ctx, outbound.Id)

	// Emit event for monitoring/alerting
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"outbound_aborted",
		sdk.NewAttribute("utx_id", utxId),
		sdk.NewAttribute("outbound_id", outbound.Id),
		sdk.NewAttribute("abort_reason", reason),
	))

	return nil
}
```

**File:** x/uexecutor/keeper/outbound.go (L102-147)
```go
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L8-25)
```go
// buildRevertOutbound creates an INBOUND_REVERT outbound with gas fields populated
// from the UniversalCore contract via getOutboundTxGasAndFees.
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}
```

**File:** x/uexecutor/keeper/evm.go (L261-303)
```go
// Calls Handler Contract to deposit prc20 tokens
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```
