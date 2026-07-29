### Title
Attacker-supplied `RevertInstructions.FundRecipient = address(0)` causes permanent burn of bridged/refund/gas-refund funds instead of falling back to sender — (File: `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The Foundation report shows that a "valid" but zero-value recipient address is treated as a legitimate recipient instead of triggering the fallback logic, causing ETH to be burned. Push Chain has the same class of bug in its revert/refund recipient resolution: `RevertInstructions.FundRecipient` is attacker-supplied (set by the depositor on the source chain), only checked for emptiness (`!= ""`), and never checked for being the zero address. `common.IsHexAddress`/`utils.IsValidAddress`, the only validation ever applied anywhere near this field, treats `0x000...000` as a perfectly valid address, so a non-empty zero-address string sails through every check.

### Finding Description
`Inbound.RevertInstructions.FundRecipient` is a user-controlled field originating from the depositor’s call to the source-chain gateway (decoded and carried through `Canonicalize()` in `x/uexecutor/types/inbound.go`, which only trims/canonicalizes the string but never rejects the zero address): [1](#0-0) 

This field is never validated in `Inbound.ValidateBasic()` or `ValidateForExecution()` — those functions only check `Sender`, `Recipient`, `Amount`, and `AssetAddr`: [2](#0-1) [3](#0-2) 

Every place that resolves a revert/refund recipient uses the same unsafe pattern — falling back to `Sender` only when `FundRecipient` is the *empty string*, not when it's the zero address:

1. `buildRevertOutbound` (used for admin reverts and inbound execution-failure reverts): [4](#0-3) 

2. `handleFailedOutbound` (re-mints bridged PRC20 back to the "revert recipient" when an outbound fails on the destination chain): [5](#0-4) 

3. `applyGasRefund` (refunds excess relayer gas fee, called on both successful and failed outbounds): [6](#0-5) 

4. `AttachRescueOutboundFromReceipt` (rescue-funds flow) uses the identical fallback pattern: [7](#0-6) 

The only "address validity" helper in the codebase, `utils.IsValidAddress(..., HEX)`, wraps `common.IsHexAddress`, which classifies the zero address as valid — it is a format check, not a non-zero check: [8](#0-7) 

Consequently, if a depositor (attacker or via integration error) sets `RevertInstructions.FundRecipient = "0x0000000000000000000000000000000000000000"` when initiating a deposit on the source chain, then any downstream failure that should refund them (inbound execution failure/revert, outbound execution failure, or gas fee excess refund) will resolve `recipient` to the zero address instead of falling back to `Sender`. `CallPRC20Deposit` / `CallUniversalCoreRefundUnusedGas` will then mint or send the bridged tokens or gas refund to `address(0)` on the target chain — a burn.

### Impact Explanation
Depositor funds (bridged principal on revert, re-minted PRC20 on outbound failure, or excess gas-fee refunds) are permanently burned to the zero address instead of being returned to the depositor. This is squarely in scope under "permanent loss ... of user or protocol-controlled funds" and "corruption of ... refund accounting". The loss is deterministic and irreversible once the outbound/refund transaction executes, matching the Medium-severity classification used in the original Foundation finding (value is "leaked"/burned, not stolen by a third party).

### Likelihood Explanation
The trigger requires only an ordinary, unprivileged depositor to submit a source-chain deposit whose `revertInstructions.fundRecipient` parameter is the zero address — either through an integration/wallet bug (most realistic path, e.g. a caller passing an unset/default address in the gateway ABI call) or deliberately. No validator collusion, privileged role, or race condition is needed; validators just faithfully vote on the attacker-supplied field, and the code paths above already exist to convert failures into reverts/refunds where the bug fires.

### Recommendation
In every recipient-resolution site (`buildRevertOutbound`, `handleFailedOutbound`, `applyGasRefund`, `AttachRescueOutboundFromReceipt`, and any future revert/refund helper), treat `FundRecipient == EvmZeroAddress` (in addition to `== ""`) as "not set" and fall back to `Sender`. Additionally, reject/flag `RevertInstructions.FundRecipient == address(0)` in `Inbound.ValidateForExecution()` (or in `Canonicalize()`) so a zero-address revert target never reaches execution silently.

### Proof of Concept
1. Attacker calls the source-chain gateway `addFunds`/bridge method, including `revertInstructions.fundRecipient = 0x0000000000000000000000000000000000000000` alongside a normal deposit.
2. Validators observe and vote the inbound; `Canonicalize()` only normalizes the string, it does not reject the zero address: [1](#0-0) 
3. Execution fails for any reason already covered by existing tests (e.g., missing token config, as in `test/integration/uexecutor/vote_inbound_validation_test.go:312-364`, or an EVM revert on the destination outbound).
4. `buildRevertOutbound` computes `recipient = inbound.RevertInstructions.FundRecipient` because the string is non-empty: [4](#0-3) 
5. The resulting `OutboundTx.Recipient` (or, in the failed-outbound re-mint path, the `common.HexToAddress(recipient)` passed to `CallPRC20Deposit`) is `address(0)`, and the bridged tokens are minted/sent to the burn address, permanently lost to the depositor.

### Citations

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

**File:** utils/address.go (L88-102)
```go
// IsValidAddress checks if the address is a valid COSMOS, HEX (0x), or EITHER address
func IsValidAddress(addr string, at AddressType) bool {
	switch at {
	case COSMOS:
		_, err := sdk.AccAddressFromBech32(addr)
		return err == nil
	case HEX:
		return common.IsHexAddress(addr)
	case EITHER:
		_, err := ConvertAnyAddressToBytes(addr)
		return err == nil
	default:
		return false
	}
}
```
