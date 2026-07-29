## Analysis: Missing zero-address check on attacker-supplied `RevertInstructions.FundRecipient`

Based on my investigation, this repository has a plausible native analog to the "addresses not checked" bug class, in the revert/refund fund-recipient path of `x/uexecutor`.

### Title
Unchecked zero-address `RevertInstructions.FundRecipient` can burn re-minted/refunded PRC20 funds - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
`Inbound.RevertInstructions.FundRecipient` is an attacker-supplied field (set by the depositor on the source chain gateway) that is canonicalized but never checked for the zero address, then flows into the outbound revert/refund logic that deposits re-minted PRC20 tokens.

### Finding Description
`Canonicalize()` only normalizes `RevertInstructions.FundRecipient` formatting per source-chain namespace; it does not reject `EvmZeroAddress` or any equivalent null value. [1](#0-0) 

`ValidateForExecution` validates `Recipient` for `FUNDS`/`GAS`/CEA cases but never validates `RevertInstructions.FundRecipient` at all. [2](#0-1) 

Downstream, when an outbound fails, `handleFailedOutbound` picks the revert recipient purely by non-empty-string check and calls `CallPRC20Deposit` to mint tokens back to it: [3](#0-2) 

Similarly, `applyGasRefund` selects the gas-refund recipient the same way, with no zero-address guard, before calling `CallUniversalCoreRefundUnusedGas`: [4](#0-3) 

If an attacker crafts an inbound (or the corresponding source-chain gateway call) with `RevertInstructions.FundRecipient` set to the EVM zero address (`0x000...000`), the string is non-empty so it bypasses the `!= ""` fallback-to-sender check in both places, and the honest validators/nodes will canonicalize and store it as-is with no rejection.

### Impact Explanation
On outbound failure or gas over-collection, PRC20 tokens are permanently sent to the zero address — an unrecoverable burn of protocol/user-controlled funds (permanent loss), rather than either failing safely or falling back to the original sender. This matches the "unauthorized … permanent loss … of user or protocol-controlled funds" allowed-impact category through the "corruption of PRC20 or native asset accounting, refund accounting … revert destination" pivot.

### Likelihood Explanation
The trigger is fully within reach of an unprivileged external user submitting a normal cross-chain deposit/payload with a self-chosen revert-instruction fund recipient — no privileged actor, malicious validator, or malicious relayer is required; honest validators/nodes will process and canonicalize the value without rejecting it.

### Recommendation
Add explicit zero-address (and any other well-known burn/null sentinel) checks for `RevertInstructions.FundRecipient` in `Inbound.ValidateForExecution` (rejecting or falling back to `Sender`), and add the same defensive check directly in `handleFailedOutbound` and `applyGasRefund` in `x/uexecutor/keeper/outbound.go` before calling `CallPRC20Deposit` / `CallUniversalCoreRefundUnusedGas`, mirroring the recommendation from the original StakingRewardsV3 report to always validate that input addresses are not the zero address.

### Proof of Concept
1. Attacker deposits funds through the source-chain gateway (or an inbound is otherwise recorded) with `TxType_FUNDS` and `RevertInstructions.FundRecipient = "0x0000000000000000000000000000000000000000"`.
2. `Canonicalize()` normalizes but does not reject the zero address; `ValidateForExecution` has no check on `RevertInstructions.FundRecipient`, so the UTX/outbound is created successfully.
3. The outbound to the destination chain later fails (e.g., destination-chain revert, insufficient gas, or contract rejection), triggering `handleFailedOutbound`.
4. Because `outbound.RevertInstructions.FundRecipient != ""`, `recipient` is set to the zero address instead of falling back to `outbound.Sender`.
5. `CallPRC20Deposit` mints the reverted funds to the zero address, permanently burning them. The same path applies to `applyGasRefund` for excess gas refunds.

**Note on verification limits:** I was not able to fully trace how `RevertInstructions.FundRecipient` is originally populated from the external-chain gateway event parsers (`universalClient/chains/evm/event_parser.go`, `universalClient/chains/svm/event_parser.go`) within the remaining tool budget, so I cannot confirm with certainty whether the gateway contracts themselves impose an off-chain/on-chain zero-address restriction before the value reaches the Push Chain node code. If such a check exists upstream, the reachable impact inside this repository would be reduced to defense-in-depth rather than a directly exploitable path. A Devin session with full repository/file access would be needed to confirm the gateway-side constraints conclusively.

### Citations

**File:** x/uexecutor/types/inbound.go (L32-35)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L200-206)
```go

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```
