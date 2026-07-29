### Title
Missing zero-address (`0x000...0`) validation on inbound/outbound recipient and revert `FundRecipient` fields allows attacker-crafted deposits to permanently burn or freeze bridged funds - (File: `x/uexecutor/types/inbound.go`, `x/uexecutor/types/outbound_tx.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/build_revert_outbound.go`)

### Summary
Push Chain's inbound/outbound validation only checks that recipient-type fields are non-empty and, in some cases, hex-formatted. None of the code paths reject the literal zero address (`0x0000000000000000000000000000000000000000`, defined as `EvmZeroAddress` in [1](#0-0) ). This is the direct analog of the M-15 finding: `_to`/recipient fields are checked for emptiness but never for `address(0)`.

### Finding Description
`Inbound.ValidateForExecution` for `FUNDS`/`GAS` types only checks `Recipient` for emptiness and hex-format validity via `utils.IsValidAddress`, which accepts the all-zero address as a syntactically valid hex address: [2](#0-1) 

The same gap exists for the CEA payload path: [3](#0-2) 

`RevertInstructions.FundRecipient` — fully attacker-controlled, since it is submitted as part of the user's own source-chain deposit event and canonicalized (not independently validated against `EvmZeroAddress`) at ingestion — is only guarded against the empty string in every consumer:
- Revert-outbound construction: [4](#0-3) 
- Failed-outbound re-mint path: [5](#0-4) 
- Gas refund path: [6](#0-5) 
- Rescue-funds path: [7](#0-6) 

`OutboundTx.ValidateBasic` similarly only requires `Recipient` to be non-empty, with no hex-format or zero-address check at all: [8](#0-7) 

All of these recipient values eventually flow into `CallPRC20Deposit` / `CallUniversalCoreRefundUnusedGas`, which issue a `DerivedEVMCall` that mints PRC20 tokens directly to the supplied address [9](#0-8) . Nothing in the Go keeper layer stops `0x000...0` from being used as the mint target.

### Impact Explanation
An ordinary, unprivileged depositor can submit a source-chain deposit whose `RevertInstructions.FundRecipient` (or, for `FUNDS`/`GAS` types, the `Recipient` itself) is the literal zero address. Two outcomes are possible depending on the PRC20 contract's `_mint` semantics (Solidity contract code was not available in this index to confirm definitively):
- If the PRC20 mint function reverts on `to == address(0)` (standard OpenZeppelin behavior), every revert/refund/rescue attempt for that inbound will keep failing at `CallPRC20Deposit`, driving the outbound into `AbortOutbound` (permanent freezing requiring manual admin intervention) — see the abort branch at [10](#0-9) .
- If the mint function does not check for the zero address, the bridged/refunded tokens are minted to `0x0` and are permanently unrecoverable (unauthorized burn-equivalent loss of protocol-bridged value).

Both outcomes match in-scope impacts ("permanent loss" / "permanent freezing" of protocol-controlled funds reachable via ordinary user deposit paths).

### Likelihood Explanation
High for triggering the code path: any unprivileged user can set `FundRecipient` or `Recipient` to `0x000...0` in their own deposit event data, and it passes both `ValidateBasic`/`ValidateForExecution` and canonicalization unmodified. The actual severity (burn vs. freeze) depends on the PRC20 Solidity contract's `_mint` guard, which is outside the indexed Go code and could not be confirmed here — this should be verified directly against the PRC20 contract source before treating this as a confirmed fund-loss primitive versus a confirmed DoS/freeze primitive.

### Recommendation
Add an explicit zero-address check (`recipient != EvmZeroAddress`, or the equivalent EVM `common.Address{}` check after `common.HexToAddress`) alongside the existing empty-string checks in:
- `Inbound.ValidateForExecution` (both the `FUNDS`/`GAS` branch and the `IsCEA` payload branch),
- `OutboundTx.ValidateBasic`,
- every `FundRecipient` fallback site (`buildRevertOutbound`, `handleFailedOutbound`, `applyGasRefund`, `AttachRescueOutboundFromReceipt`),

so that inbounds/outbounds carrying a zero-address recipient are rejected the same way empty recipients are today (failed PCTx + revert, per the existing pattern in [11](#0-10) ), rather than being minted or aborted downstream.

### Proof of Concept
1. Attacker deposits on a source chain (e.g. EVM testnet) with `TxType_FUNDS`, a valid amount/asset, but sets the observed inbound's `Recipient` (or `RevertInstructions.FundRecipient`) to `0x0000000000000000000000000000000000000000`.
2. Honest validators observe and vote the event faithfully (per the "honest validators/nodes" assumption); `ValidateBasic`/`ValidateForExecution` accept it because both checks only look for emptiness/hex-format, not the zero value ( [2](#0-1) ).
3. On any subsequent revert/refund/abort trigger for this UTX (e.g. outbound execution failure), `handleFailedOutbound` picks `recipient = outbound.RevertInstructions.FundRecipient` unchanged ( [5](#0-4) ) and calls `CallPRC20Deposit(ctx, prc20Addr, common.HexToAddress(recipient), amount)` with `recipient == 0x0`.
4. Depending on PRC20's `_mint` guard, either the funds are minted to and lost at `0x0`, or the call reverts and the outbound is permanently `ABORTED` via [12](#0-11) , both of which require no privileged action to trigger and both of which are within the stated impact gate.

### Citations

**File:** x/uexecutor/types/inbound.go (L14-14)
```go
const EvmZeroAddress = "0x0000000000000000000000000000000000000000"
```

**File:** x/uexecutor/types/inbound.go (L156-161)
```go
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
```

**File:** x/uexecutor/types/inbound.go (L165-171)
```go
	case TxType_FUNDS, TxType_GAS:
		if strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
		}
		if !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address: %s", p.Recipient)
		}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-14)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}
```

**File:** x/uexecutor/keeper/outbound.go (L107-119)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/keeper/outbound.go (L201-206)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** x/uexecutor/keeper/create_outbound.go (L295-300)
```go
		// Rescued funds go to the original revert recipient (or the sender as fallback).
		recipient := originalUtx.InboundTx.Sender
		if originalUtx.InboundTx.RevertInstructions != nil &&
			originalUtx.InboundTx.RevertInstructions.FundRecipient != "" {
			recipient = originalUtx.InboundTx.RevertInstructions.FundRecipient
		}
```

**File:** x/uexecutor/types/outbound_tx.go (L34-37)
```go
	// recipient must not be empty
	if strings.TrimSpace(p.Recipient) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
	}
```

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L8-12)
```go
// handleFailedInboundValidation records a failed PCTx on the UTX and, for non-isCEA
// inbounds, schedules an INBOUND_REVERT outbound so the user's funds can be returned
// on the source chain. This is called when ValidateForExecution fails after the ballot
// has already been finalized and the UTX created.
func (k Keeper) handleFailedInboundValidation(sdkCtx sdk.Context, utx types.UniversalTx, validationErr error) error {
```
