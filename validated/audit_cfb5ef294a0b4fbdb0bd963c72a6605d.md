## Analog Vulnerability Found

### Title
Unvalidated `RevertInstructions.FundRecipient` allows bridged funds to be irrecoverably burned/misrouted during outbound revert and gas-refund flows - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
The Sherlock report describes a bug class where a user-supplied "receiver" address is never validated, so a malicious or careless actor can cause the receiver-bound value to become permanently stuck (or, in the worse variant, destroyed) with no recourse. Push Chain's `x/uexecutor` module has the same root cause: `RevertInstructions.FundRecipient` — the address that receives re-minted PRC20 tokens whenever a bridged outbound fails or excess gas needs refunding — is taken verbatim from attacker-supplied data and is never checked against the zero address or any other unusable/malicious value before the mint call is issued.

### Finding Description
`RevertInstructions.FundRecipient` on an `OutboundTx` is populated straight from the Solidity `UniversalTxOutbound` event's `revertRecipient` field, which is a plain caller-supplied `address` parameter on the withdraw/bridge-out call executed via the user's UEA: [1](#0-0) 

That value flows unchanged into `handleFailedOutbound`, which mints PRC20 tokens back to it whenever an outbound observation reports failure: [2](#0-1) 

The same unchecked value is used again for excess-gas refunds: [3](#0-2) 

And again for admin rescue payouts: [4](#0-3) 

Nowhere in `ValidateBasic` or `ValidateForExecution` — the only two validation gates for `Inbound`/`RevertInstructions` data — is `FundRecipient` checked for being non-zero or otherwise usable. `ValidateForExecution` only validates the *forward* `Recipient` field for `FUNDS`/`GAS` types, never `RevertInstructions.FundRecipient`: [5](#0-4) 

`Canonicalize()` explicitly documents that it is "lenient" and never rejects malformed/unusable values, only trims them: [6](#0-5) 

Because `common.HexToAddress` silently returns the zero address for empty/garbage/unparseable input rather than erroring, and because `address(0)` is itself a perfectly valid Solidity `address` value an unprivileged caller can pass directly when constructing the original bridge-out transaction, any depositor (or a malicious dApp/session key acting through a victim's UEA-signed payload) can set `revertRecipient = address(0)`. If the resulting outbound later fails on the destination chain — which is routine (insufficient destination gas, paused destination contract, bad target, etc., all attacker/user-triggerable) — `handleFailedOutbound` re-mints the full bridged amount to the zero address, and `applyGasRefund` sends any excess prepaid gas there too. Minting to the zero address on a standard PRC20 does not revert, so `AbortOutbound`'s "manual intervention" safety net (triggered only on remint *failure*) never fires, and the tokens are burned with no operator visibility.

### Impact Explanation
This is a direct analog to the Sherlock finding: an attacker-controlled "receiver"/fund-recipient address that is never validated causes the depositor's own bridged funds to be permanently and silently destroyed the moment the revert/refund code path executes — matching the "unauthorized burn ... of user or protocol-controlled funds" and "refund accounting ... revert destination ... must not misroute value" impact categories explicitly in scope. Unlike the original Sherlock case (which only "locks" funds behind a revert), this Push Chain variant is strictly worse: because the destination address is `address(0)` rather than a blocklisted account, the PRC20 mint does not revert, so funds are irrecoverably burned rather than merely stuck, and no `ABORTED` state is raised to alert operators.

### Likelihood Explanation
Reachability requires only an ordinary, unprivileged user (or a third-party contract executing on their behalf via a signed universal payload) constructing a bridge-out transaction with `revertRecipient = 0x0` and having that outbound subsequently fail to be observed by honest Universal Validators — both are routine, attacker-triggerable conditions with no privileged access needed.

### Recommendation
Add an explicit check in `ValidateForExecution` (for the inbound `RevertInstructions.FundRecipient`) and in `BuildOutboundsFromReceipt`/`handleFailedOutbound`/`applyGasRefund` (for the outbound `RevertInstructions.FundRecipient`) that rejects or falls back to `outbound.Sender`/`inbound.Sender` whenever the fund recipient is the zero address or otherwise fails a well-formedness check, before any `CallPRC20Deposit`/`CallUniversalCoreRefundUnusedGas` call is issued.

### Proof of Concept
1. User's UEA (or a malicious contract it interacts with) calls the Push Chain gateway's withdraw path with `revertRecipient = address(0)`, bridging PRC20 tokens out to an external chain — see the event shape decoded in [7](#0-6) .
2. `BuildOutboundsFromReceipt` stores this unchecked value as `outbound.RevertInstructions.FundRecipient = "0x000...000"` (`x/uexecutor/keeper/create_outbound.go:86-88`).
3. The destination-chain execution fails (e.g., insufficient gas or invalid call), and Universal Validators honestly vote `MsgVoteOutbound{success:false}`.
4. `handleFailedOutbound` computes `recipient = outbound.RevertInstructions.FundRecipient` (zero address) and calls `k.CallPRC20Deposit(ctx, prc20, common.HexToAddress(recipient), amount)`, permanently minting the bridged amount to `address(0)` with no error and no `ABORTED` status raised.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L86-88)
```go
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
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

**File:** x/uexecutor/keeper/outbound.go (L102-119)
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
```

**File:** x/uexecutor/keeper/outbound.go (L198-206)
```go
	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** x/uexecutor/types/inbound.go (L16-35)
```go
// Canonicalize normalizes encoding-variant fields in place (per source-chain
// namespace) so the same event from any observer is byte-identical across
// ballot keys, UTX keys and registry lookups. Lenient (unparseable values are
// kept trimmed, never rejected) because the vote path must always record a
// UTX — execution-level validation rejects malformed inbounds later.
func (p *Inbound) Canonicalize() {
	p.SourceChain = strings.TrimSpace(p.SourceChain)
	p.TxHash = utils.LenientCanonicalizeTxHash(p.SourceChain, p.TxHash)
	p.Sender = utils.LenientCanonicalizeAddress(p.SourceChain, p.Sender)
	p.AssetAddr = utils.LenientCanonicalizeAddress(p.SourceChain, p.AssetAddr)
	// Recipient lives on Push Chain (EVM) regardless of source chain.
	p.Recipient = utils.LenientCanonicalizeEVMAddress(p.Recipient)
	p.LogIndex = strings.TrimSpace(p.LogIndex)
	p.Amount = strings.TrimSpace(p.Amount)
	p.RawPayload = utils.CanonicalizeHexBlob(p.RawPayload)
	p.VerificationData = utils.CanonicalizeHexBlob(p.VerificationData)
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
```

**File:** x/uexecutor/types/inbound.go (L150-172)
```go
	// Validate fields required per tx_type
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
	case TxType_FUNDS, TxType_GAS:
		if strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
		}
		if !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address: %s", p.Recipient)
		}
	}
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L61-92)
```go
		{Type: addressType}, // revertRecipient
		{Type: uint8Type},   // txType
		{Type: uint256Type}, // gasPrice
	}

	values, err := arguments.Unpack(log.Data)
	if err != nil {
		return nil, fmt.Errorf("failed to unpack UniversalTxOutbound: %w", err)
	}

	if len(values) != 11 {
		return nil, fmt.Errorf("unexpected number of unpacked values: %d", len(values))
	}

	i := 0
	event.ChainId = values[i].(string)
	i++
	event.Target = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.Amount = values[i].(*big.Int)
	i++
	event.GasToken = values[i].(common.Address).Hex()
	i++
	event.GasFee = values[i].(*big.Int)
	i++
	event.GasLimit = values[i].(*big.Int)
	i++
	event.Payload = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.ProtocolFee = values[i].(*big.Int)
	i++
	event.RevertRecipient = values[i].(common.Address).Hex()
```
