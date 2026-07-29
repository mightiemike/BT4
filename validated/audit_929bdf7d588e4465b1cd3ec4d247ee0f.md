## Title
Unvalidated `RevertInstructions.FundRecipient` / withdraw `revertRecipient` allows attacker-controlled refunds to resolve to the zero address, permanently burning bridged funds - (File: x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/build_revert_outbound.go, x/uexecutor/keeper/create_outbound.go)

### Summary
The external report's root cause is a setter that accepts `address(0)` without validation, causing a downstream fund-movement function to silently no-op. The exact structural analog in Push Chain is the reverse-but-equivalent failure mode: `RevertInstructions.FundRecipient` is accepted from unprivileged, attacker-controlled input (inbound deposit calldata on the source chain, or the `revertRecipient` parameter of `UniversalGatewayPC.withdraw`) and is used verbatim as the mint/refund destination without validating that it decodes to a real (non-zero) address, exactly like the missing `_newCoverageFund != address(0)` check in the original report.

### Finding Description
Multiple refund/revert code paths take a user-supplied `FundRecipient` string and only check it is non-empty before using it as the destination of a PRC20 re-mint:

- `buildRevertOutbound` (`x/uexecutor/keeper/build_revert_outbound.go:11-14`) uses `inbound.RevertInstructions.FundRecipient` if `!= ""` as the `INBOUND_REVERT` recipient. [1](#0-0) 

- `handleFailedOutbound` (`x/uexecutor/keeper/outbound.go:107-119`) re-mints bridged PRC20 tokens to `outbound.RevertInstructions.FundRecipient` (falling back to `outbound.Sender`), then converts it with `common.HexToAddress(recipient)` with no zero-address/format check before calling `CallPRC20Deposit`. [2](#0-1) 

- `applyGasRefund` (`x/uexecutor/keeper/outbound.go:201-206`) does the same for the excess-gas refund path. [3](#0-2) 

- `AttachRescueOutboundFromReceipt` (`x/uexecutor/keeper/create_outbound.go:295-300`) reuses `originalUtx.InboundTx.RevertInstructions.FundRecipient` unchecked as the rescue-fund recipient. [4](#0-3) 

`FundRecipient` originates from `event.RevertRecipient` decoded from the `UniversalTxOutbound` EVM log (`x/uexecutor/keeper/create_outbound.go:86-88`), i.e. it is fully attacker-controlled calldata passed to `UniversalGatewayPC.withdraw`, and separately from the raw inbound payload on the source chain (`Inbound.RevertInstructions`, canonicalized in `x/uexecutor/types/inbound.go:32-35`). [5](#0-4) [6](#0-5) 

Go-ethereum's `common.HexToAddress` silently returns the all-zero address for any string that isn't valid 20-byte hex (it decodes what it can and pads/truncates rather than erroring). Because none of these code paths reject `FundRecipient == "0x0000000000000000000000000000000000000000"` or malformed hex, an attacker can cause `CallPRC20Deposit`/`CallUniversalCoreRefundUnusedGas` to mint PRC20 tokens to the burn address, exactly mirroring the unpatched `CoverageFundAddress.set`/`River.setCoverageFund` pattern where a zero-value field silently degrades a fund-movement function instead of reverting.

### Impact Explanation
Whenever an outbound execution fails and the protocol re-mints the bridged asset back on Push Chain (`FinalizeOutbound` → `handleFailedOutbound`), or whenever excess gas is refunded (`applyGasRefund`), or a stuck inbound is rescued (`AttachRescueOutboundFromReceipt`), the destination address is derived from attacker-supplied `FundRecipient`/`revertRecipient` without a zero-address or format check. If that value resolves to the zero address, the re-minted/refunded PRC20 tokens are irrecoverably burned — a permanent loss of user funds. This matches the "permanent loss ... of user or protocol-controlled funds" category in the impact gate, and is a direct structural analog of the CoverageFundAddress issue (setter with no zero-address guard breaking a downstream fund-movement invariant).

### Likelihood Explanation
The trigger requires only an ordinary unprivileged user submitting an inbound deposit with a malformed/zero `fund_recipient`, or calling `UniversalGatewayPC.withdraw` with a zero/garbage `revertRecipient`, and then having their own outbound/execution subsequently fail (a state reachable through ordinary paths such as insufficient liquidity, PRC20 swap failure, or destination-chain gas-price spikes). No validator collusion, admin action, or privileged control is needed — quorum-honest validators would still finalize and observe the failure exactly as designed, and the burn happens purely due to the missing address check.

### Recommendation
Add a hard zero-address / format validation on `RevertInstructions.FundRecipient` (and the withdraw event's `revertRecipient`) at the earliest ingress point — `Inbound.Canonicalize`/`ValidateBasic` for inbound-side revert instructions, and `BuildOutboundsFromReceipt` for outbound-side ones — rejecting `""`, the zero address, and any value that `common.HexToAddress`/canonicalization would silently collapse to zero. If validation fails, fall back explicitly to `Sender` (as already done for the empty-string case) rather than allowing an ambiguous/zero value to reach `CallPRC20Deposit` or `CallUniversalCoreRefundUnusedGas`.

### Proof of Concept
1. Attacker submits (or an off-chain relay observes) an inbound `FUNDS`/`GAS_AND_PAYLOAD` deposit whose `RevertInstructions.FundRecipient` is set to `"0x0000000000000000000000000000000000000000"` (or any invalid hex string such as `"zz"`), which canonicalizes to a non-empty string and is treated as a valid override in `buildRevertOutbound`.
2. The corresponding outbound execution on the destination/external chain fails (reachable via normal conditions: insufficient PRC20 liquidity for auto-swap, external-chain gas spike, etc.), driving `FinalizeOutbound` → `handleFailedOutbound`.
3. `handleFailedOutbound` computes `recipient := outbound.RevertInstructions.FundRecipient` (non-empty, so the override is used) and calls `common.HexToAddress(recipient)`, which resolves to `0x000...000`.
4. `CallPRC20Deposit` mints the bridged PRC20 amount to the zero address — the tokens are permanently lost, with no revert or rejection anywhere in the flow.

**Uncertainty note:** I was unable to locate a `ValidateBasic`/format-validation function specifically for `RevertInstructions` or `OutboundTx` in `x/uexecutor/types` (only `Inbound.Canonicalize` was found, which normalizes but does not reject malformed/zero addresses); I could not fully confirm whether any downstream EVM-side precompile call (`depositPRC20Token`) itself rejects a zero `to` address, since that logic lives in the Solidity `UniversalCore` contract, which is out of this repository's index. If the Solidity contract already reverts on `to == address(0)`, this finding would be mitigated at that layer instead — this should be verified directly against `UniversalCore.sol`/`depositPRC20Token` before treating this as fully unmitigated.

### Citations

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

**File:** x/uexecutor/types/inbound.go (L21-35)
```go
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
