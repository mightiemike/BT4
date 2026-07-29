Based on my research, I found a valid analog to the Lido bug report in Push Chain's revert/refund flow.

### Title
Unvalidated attacker-controlled `RevertInstructions.FundRecipient` address can permanently strand refunded/re-minted funds - (File: `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external Lido report flags a constructor that assigns an address parameter (`TREASURY`) to a critical variable without validating it is non-zero, risking permanent misconfiguration. The Push Chain analog is `Inbound.RevertInstructions.FundRecipient` — a user/attacker-supplied field carried on the inbound (submitted by the depositor on the source chain and relayed by honest UVs) that is used, unvalidated, as the destination for refunded or re-minted funds on both the outbound-revert path and the failed-outbound re-mint path.

### Finding Description
`FundRecipient` originates from `types.RevertInstructions` on the `Inbound` message [1](#0-0) . `Canonicalize` only lenient-canonicalizes the string (never rejects it) before it is persisted with the UTX [2](#0-1) , and neither `ValidateBasic` nor `ValidateForExecution` validate the format/non-emptiness of `RevertInstructions.FundRecipient` anywhere in `x/uexecutor/types/inbound.go` [3](#0-2) [4](#0-3) .

This unvalidated value is later used directly as the destination address in two fund-moving paths:
1. `buildRevertOutbound`, which uses `FundRecipient` as the outbound `Recipient` when constructing an `INBOUND_REVERT` outbound for a failed/expired inbound [5](#0-4) .
2. `handleFailedOutbound`, which uses `outbound.RevertInstructions.FundRecipient` as the recipient of a PRC20 re-mint (`CallPRC20Deposit`) when an outbound fails on the destination chain, converting it directly via `common.HexToAddress(recipient)` with no address validation [6](#0-5) .

`common.HexToAddress` (go-ethereum) does not error on malformed input — it silently truncates/pads arbitrary strings into a 20-byte address, so a garbage or empty-after-canonicalization value converts into some address (potentially the zero address or an unintended address) rather than failing loudly.

### Impact Explanation
If an attacker (the source-chain depositor, an unprivileged actor from Push Chain's perspective) supplies a malformed, garbage, or zero-mapping `FundRecipient` when initiating a deposit/inbound, and that inbound later fails execution or its corresponding outbound fails on the destination chain, the refund/re-mint of the bridged/native funds is minted or routed to whatever address `common.HexToAddress` happens to derive — which can be the zero address or an address nobody controls. Because this happens inside the automatic revert/re-mint flow (not user-editable after the fact), the funds become **permanently unrecoverable**, matching the "permanent loss" impact class in scope. This is self-inflicted (the attacker griefs their own funds) but it can also occur unintentionally for any legitimate user who fat-fingers or omits validation client-side, and there is no on-chain guard preventing it — mirroring the constructor-bug pattern where a bad but unchecked address value requires redeployment/irreversible loss with no recovery path (here, the only recovery is the manual `ABORT` path triggered when `CallPRC20Deposit` itself errors, not when it silently succeeds against a wrong address).

### Likelihood Explanation
Likelihood is moderate: it requires a legitimate quorum-approved inbound to later hit a failure or an outbound-failure condition, and it requires the attacker/user to have submitted a malformed `FundRecipient` at deposit time on the source chain. Since `RevertInstructions` is fully user/attacker-controlled input propagated by honest UVs with no format validation anywhere in the pipeline, the trigger is straightforward and requires no privileged access — an ordinary depositor can supply this value.

### Recommendation
Validate `RevertInstructions.FundRecipient` for format (valid non-zero hex/EVM address, matching the `utils.IsValidAddress(..., utils.HEX)` check already used for `Recipient` in `ValidateForExecution`) either in `Inbound.ValidateForExecution` or immediately before use in `buildRevertOutbound` / `handleFailedOutbound`. If the field is empty or invalid, fall back explicitly to `inbound.Sender` / `outbound.Sender` (as already happens for the empty-string case) rather than passing an unchecked string into `common.HexToAddress`.

### Proof of Concept
1. Submit a cross-chain deposit (`FUNDS` or `FUNDS_AND_PAYLOAD` inbound) with `RevertInstructions.FundRecipient` set to a non-hex or zero-mapping string (e.g., `"not-a-real-address"` or a string that canonicalizes to `0x0000...0000`).
2. Let honest UVs reach quorum on the vote; the UTX is created with the malformed `FundRecipient` persisted verbatim (only lenient canonicalization applied, no rejection) [2](#0-1) .
3. Trigger execution failure (e.g., token config lookup failure, deposit failure) or a subsequent outbound failure on the destination chain.
4. Observe `buildRevertOutbound`/`handleFailedOutbound` route the refund/re-mint to `common.HexToAddress(FundRecipient)` [5](#0-4) [7](#0-6) , silently sending funds to an address that does not correspond to the intended recipient, with no on-chain check to catch or prevent this before the mint/send executes.

### Citations

**File:** x/uexecutor/types/types.pb.go (L486-488)
```go
type RevertInstructions struct {
	FundRecipient string `protobuf:"bytes,1,opt,name=fund_recipient,json=fundRecipient,proto3" json:"fund_recipient,omitempty"`
}
```

**File:** x/uexecutor/types/inbound.go (L32-35)
```go
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
```

**File:** x/uexecutor/types/inbound.go (L90-121)
```go
func (p Inbound) ValidateBasic() error {
	// Validate source_chain (must follow CAIP-2 format) — needed for UTX key
	chain := strings.TrimSpace(p.SourceChain)
	if chain == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "source chain cannot be empty")
	}
	if !strings.Contains(chain, ":") {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "source chain must be in CAIP-2 format <namespace>:<reference>")
	}

	// Validate tx_hash — needed for UTX key
	if strings.TrimSpace(p.TxHash) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "tx_hash cannot be empty")
	}

	// Validate sender — needed for revert recipient fallback
	if strings.TrimSpace(p.Sender) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "sender cannot be empty")
	}

	// Validate log_index — needed for UTX key
	if strings.TrimSpace(p.LogIndex) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "log_index cannot be empty")
	}

	// Validate tx_type enum — needed to route execution
	if _, ok := TxType_name[int32(p.TxType)]; !ok || p.TxType == TxType_UNSPECIFIED_TX {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid tx_type: %v", p.TxType)
	}

	return nil
}
```

**File:** x/uexecutor/types/inbound.go (L126-175)
```go
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}

	// Validate asset_addr
	if strings.TrimSpace(p.AssetAddr) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "asset_addr cannot be empty")
	}

	// isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD
	if p.IsCEA && p.TxType != TxType_FUNDS && p.TxType != TxType_FUNDS_AND_PAYLOAD && p.TxType != TxType_GAS_AND_PAYLOAD {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD tx types, got: %v", p.TxType)
	}

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

	return nil
}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-18)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
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
