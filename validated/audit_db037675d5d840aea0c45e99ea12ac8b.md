### Title
Unvalidated `RevertInstructions.FundRecipient` / `revertRecipient` allows attacker-chosen zero-address to permanently burn refunded and gas-refund funds - (File: x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/build_revert_outbound.go, x/uexecutor/types/inbound.go)

### Summary
`RevertInstructions.FundRecipient` is a fully attacker-controlled field, read verbatim from the `revertRecipient` word of a user's own `sendFunds`/gateway call on the source chain, and it is never checked for being the zero address (or otherwise invalid) anywhere in the validation pipeline. It is used directly as the mint/refund destination for bridged funds and excess gas whenever a deposit fails or an outbound reverts. A depositor who (by mistake or maliciously against themselves — but also usable to "trap" protocol-side refunds) sets this field to `0x0` gets their reverted principal and/or gas refund permanently minted to `address(0)` on Push Chain, i.e. burned with no recovery path. This is the direct analog of the Gearbox `CreditManager.sol` bug (`to = address(0)` with no `to != address(0)` guard) but applied to Push Chain's inbound-revert / gas-refund accounting.

### Finding Description
1. The attacker (any user submitting a deposit on a source chain to the Push Chain gateway) fully controls the `revertRecipient` argument of their own transaction, which is decoded from event Word 3 in `universalClient/chains/evm/event_parser.go` (`payload.RevertFundRecipient = ethcommon.BytesToAddress(w[12:32]).Hex()`) with no validation, including no rejection of the zero address. It ultimately becomes `Inbound.RevertInstructions.FundRecipient`.

2. On the core-validator side, `Inbound.Canonicalize()` only canonicalizes the address format — it does not reject or block the zero address: [1](#0-0) 

3. `ValidateForExecution` validates `Recipient` (the destination-chain recipient for `FUNDS`/`GAS` types) but never validates `RevertInstructions.FundRecipient` at all: [2](#0-1) 

4. When inbound execution or validation fails, `buildRevertOutbound` copies `FundRecipient` straight into `outbound.Recipient` with only an empty-string check, no zero-address check: [3](#0-2) 

5. That outbound recipient is later used directly as the mint target for the re-minted bridged tokens in `handleFailedOutbound`: [4](#0-3) 

6. Similarly, `applyGasRefund` computes the refund recipient the same unchecked way and calls `CallUniversalCoreRefundUnusedGas`, which itself calls `CallPRC20Deposit`/`depositPRC20Token`-equivalent logic straight to that address: [5](#0-4) 

7. The same unchecked pattern repeats for outbound-side reverts (`RevertInstructions` attached at outbound creation from the `UniversalTxWithdraw` event's `RevertRecipient`, also attacker-controlled from the Push-Chain-side smart contract call) in `create_outbound.go`: [6](#0-5) 

and for the rescue-funds flow, which falls back to the same unchecked `FundRecipient`: [7](#0-6) 

None of these paths call `utils.IsValidAddress`, `CanonicalizeEVMAddress`'s zero-address rejection (there is none — it only validates hex length, not that the value isn't all zeros), or any explicit `recipient != 0x0` check before minting/depositing funds. `common.HexToAddress("")` and `common.HexToAddress("0x0000...0")` both resolve to the EVM zero address, and `CallPRC20Deposit`/`CallUniversalCoreRefundUnusedGas` will happily mint PRC20 balance to `address(0)`, which is unrecoverable.

### Impact Explanation
This falls squarely within "permanent loss ... of user or protocol-controlled funds" and "corruption of ... refund accounting." Any inbound whose deposit later fails validation/execution (e.g., token config not found, invalid payload, contract call failure) or whose outbound is later observed as failed, will trigger `buildRevertOutbound`/`handleFailedOutbound`/`applyGasRefund` using the attacker-supplied `FundRecipient` with zero defensive check. If that value is `address(0)`, the bridged principal and/or excess gas refund is minted to the burn address and is permanently, irrecoverably lost — exactly analogous to the cited Gearbox `to=address(0)` bug.

### Likelihood Explanation
Trivial to trigger from the unprivileged attacker's own transaction: the attacker only needs to encode `revertRecipient = 0x0` in their own `sendFunds`/gateway call and then cause (or simply experience) a deposit/execution/outbound failure, which is a normal and reachable failure mode (e.g., unsupported token, insufficient gas, malformed payload, or a genuinely failed outbound on the destination chain). No validator collusion, no privileged action, and no external-chain compromise is required — only honest nodes processing an ordinary user-submitted event with a malformed field. The main leverage this gives an attacker over others is limited (it mostly self-harms the funder), but it is nevertheless an unguarded fund-loss path with a clear missing invariant identical in class to the referenced report, and it is also reachable through automated/relay tooling that blindly forwards event data without sanity-checking the recipient.

### Recommendation
Add an explicit non-zero-address (and well-formed) check for `RevertInstructions.FundRecipient` at the earliest possible point:
- In `Inbound.ValidateForExecution()` (and the analogous outbound-side validation), reject or fall back to `Sender` when `RevertInstructions != nil && (FundRecipient == "" || FundRecipient == types.EvmZeroAddress)`.
- In `buildRevertOutbound` and `applyGasRefund`, add a guard: `if recipient == "" || recipient == types.EvmZeroAddress { recipient = fallbackSender }` before using it as a mint/refund destination.
- Apply the same guard to the outbound-created `RevertInstructions.FundRecipient` in `create_outbound.go` and to the rescue-funds fallback recipient logic.

### Proof of Concept
1. Attacker calls the source-chain gateway `sendFunds`-style function, setting `revertRecipient = 0x0000000000000000000000000000000000000000` in the ABI-encoded event data (Word 3), while depositing a real, valuable asset amount, and referencing a token/asset config that will fail (or intentionally malformed payload) so that `ValidateForExecution`/execution fails.
2. `universalClient` parses this into `payload.RevertFundRecipient = "0x000...0"` (`universalClient/chains/evm/event_parser.go:259-262`), which becomes `Inbound.RevertInstructions.FundRecipient` after quorum and `Canonicalize()`.
3. Ballot finalizes; `handleFailedInboundValidation`/`ExecuteInboundGasAndPayload` calls `buildRevertOutbound`, setting `outbound.Recipient = "0x000...0"` (since it is non-empty) with no zero-address rejection.
4. The revert outbound reaches `handleFailedOutbound` (if source-chain settlement also fails) or the initial re-mint path, calling `k.CallPRC20Deposit(ctx, prc20Addr, common.HexToAddress("0x000...0"), amount)`, permanently minting the bridged PRC20 balance to the zero address.
5. Verify on-chain: `PcRevertExecution.Status == "SUCCESS"`, `ob.Recipient == types.EvmZeroAddress`, and the PRC20 balance of `address(0)` increased by `amount` with no way to reclaim it — confirming permanent loss.

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

**File:** x/uexecutor/keeper/outbound.go (L198-223)
```go
	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
```

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
