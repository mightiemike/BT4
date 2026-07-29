### Title
Unvalidated attacker-controlled `RevertInstructions.FundRecipient` silently coerces to the EVM zero address, permanently burning bridged funds on revert/refund paths - (File: `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/types/inbound.go`)

### Summary
The external report's root cause is a refund/recipient address that is accepted from an untrusted, attacker/caller-controlled field and used verbatim to route value, without verifying the address is actually redeemable at the destination — causing funds to be silently blackholed. Push Chain has a structural analog: `Inbound.RevertInstructions.FundRecipient` is a free-form, user-supplied string decoded straight off the source-chain gateway event, is explicitly **never rejected** during canonicalization or `ValidateBasic`, and is later fed directly into `common.HexToAddress()` on the Push Chain execution/refund path. Go-ethereum's `HexToAddress` does not error on malformed input — it silently decodes whatever it can and zero-pads the rest, so a malformed `FundRecipient` (or `Sender` fallback) resolves to the EVM zero address (or an unintended address), and bridged/refunded PRC20 tokens are minted there and permanently lost.

### Finding Description
`Inbound.Canonicalize()` explicitly documents a "lenient" policy for `RevertInstructions.FundRecipient`: unparseable values are kept as the trimmed raw input rather than being rejected, with the stated justification that "execution-level validation rejects malformed inbounds later": [1](#0-0) 

But no execution-level validation actually enforces that `RevertInstructions.FundRecipient` (or the `Sender` fallback) is a well-formed 20-byte EVM address before it is used to move funds. `buildRevertOutbound` picks the recipient straight from the unvalidated field: [2](#0-1) 

That recipient string is later converted directly with `common.HexToAddress(recipient)` when minting PRC20 back on a failed/reverted outbound and when refunding excess gas, with no error path if the conversion doesn't represent a real address: [3](#0-2) [4](#0-3) 

`common.HexToAddress` (go-ethereum) never returns an error; on malformed hex it decodes best-effort and zero-pads, so a bad `FundRecipient` (or `Sender`) resolves to `0x000...000` or some unintended address rather than causing a revert. `Inbound.ValidateBasic()` (referenced in tests) checks `source_chain`, `tx_hash`, `sender` non-emptiness, `log_index`, and `tx_type`, but performs no address-format check on `RevertInstructions.FundRecipient`: [5](#0-4) 

This same unvalidated recipient value is reused across every refund/revert code path in the module: `handleFailedOutbound`, `applyGasRefund` (both funds-revert and gas-refund legs), `AttachRescueOutboundFromReceipt`, `handleFailedInboundValidation`, and the admin `RevertStuckInbound` escape hatch — all fall back to the same pattern of "use `RevertInstructions.FundRecipient` if set, else `Sender`" without ever validating either is a real, redeemable address: [6](#0-5) [7](#0-6) 

### Impact Explanation
Any ordinary, unprivileged user constructing their own crosschain inbound (via the source-chain gateway) can supply a malformed `RevertInstructions.FundRecipient` — deliberately or accidentally (e.g. wrong checksum, truncated hex, non-hex string, or a value canonicalized under the wrong CAIP-2 namespace). If the payload later needs to be reverted or its excess gas refunded (execution failure, chain-outbound-disabled, invalid token config, TSS-signed outbound failure, expired-ballot admin revert, etc. — all reachable via honest validator/relayer flows with no privileged actor needed), the module mints the PRC20 revert/refund amount to `common.HexToAddress(FundRecipient)`, which silently resolves to the zero address or an unintended address instead of erroring. This is a **permanent, unrecoverable loss of bridged user funds** — the PRC20 tokens are minted to an address nobody controls, with no on-chain mechanism to recover them (`Status_REVERTED`/`PcRevertExecution.Status = "SUCCESS"` is recorded even though the funds are unspendable). This matches the "permanent loss ... of user ... funds" and "corruption of ... refund accounting" impact categories in scope.

### Likelihood Explanation
High reachability: any user submitting a normal cross-chain deposit chooses their own `RevertInstructions.FundRecipient` in the source-chain gateway call; no validator or protocol cooperation beyond the honest, standard inbound/outbound voting flow is required to reach the vulnerable refund code paths. The condition (execution failure / outbound failure / ballot expiry) is a normal, expected occurrence in cross-chain messaging (destination gas underestimation, disabled chains, temporary token-config gaps), not an edge case requiring adversarial validator behavior. The explicit "lenient, never rejected" design decision documented in `Canonicalize()` guarantees this gap is not accidental but a deliberate simplification that was not compensated for at the money-movement call sites.

### Recommendation
- Add strict address-format validation for `RevertInstructions.FundRecipient` in `Inbound.ValidateBasic()` (or in a pre-execution validation step) that rejects (or explicitly aborts to a safe manual-recovery state) any value that does not canonicalize into the destination namespace's real address format, rather than silently falling back to a trimmed/garbage string.
- At every fund-movement call site (`handleFailedOutbound`, `applyGasRefund`, `AttachRescueOutboundFromReceipt`, `buildRevertOutbound`, `handleFailedInboundValidation`), verify `common.HexToAddress(recipient)` corresponds to a syntactically valid, non-zero address (`utils.CanonicalizeEVMAddress` strict variant) before minting; on failure, route to `AbortOutbound`/a manual-intervention state instead of silently emitting a "SUCCESS" refund to zero/garbage address.
- Consider using the strict `CanonicalizeAddressByNamespace` (which returns an error) at the point value is about to move, even though the lenient variant is intentionally used earlier for ballot/key derivation.

### Proof of Concept
1. A user calls the source-chain gateway `addFunds`/similar entry point to bridge tokens into Push Chain, supplying `revert_instructions.fund_recipient = "not-a-valid-address"` (or a truncated/malformed hex string) alongside a normal `sender`/`amount`.
2. Universal Validators observe and vote the inbound; `Canonicalize()` cannot parse the malformed value and keeps it as the trimmed raw string (per `x/uexecutor/types/inbound.go` lines 32-35); `ValidateBasic()` does not reject it.
3. Push Chain executes the inbound payload; suppose execution fails for an unrelated benign reason (e.g., destination-chain outbound temporarily disabled, or gas-swap failure) — a common occurrence, not an attack.
4. `handleFailedOutbound`/`buildRevertOutbound` selects `recipient := outbound.RevertInstructions.FundRecipient` (the malformed string) and calls `k.CallPRC20Deposit(ctx, prc20Addr, common.HexToAddress(recipient), amount)`.
5. `common.HexToAddress` on the malformed string returns the zero address (or an unintended address) with no error; the PRC20 mint succeeds, `PcRevertExecution.Status = "SUCCESS"` is recorded, and the user's bridged funds are permanently unrecoverable at `0x000...0` on Push Chain.

### Citations

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

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-25)
```go
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

**File:** x/uexecutor/types/inbound_test.go (L24-114)
```go
	tests := []struct {
		name        string
		inbound     types.Inbound
		expectError bool
		errContains string
	}{
		{
			name:        "valid inbound",
			inbound:     validInbound,
			expectError: false,
		},
		{
			name: "empty source chain",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.SourceChain = ""
				return ib
			}(),
			expectError: true,
			errContains: "source chain cannot be empty",
		},
		{
			name: "invalid source chain format",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.SourceChain = "eip155" // missing ":"
				return ib
			}(),
			expectError: true,
			errContains: "CAIP-2 format",
		},
		{
			name: "empty tx_hash",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.TxHash = ""
				return ib
			}(),
			expectError: true,
			errContains: "tx_hash cannot be empty",
		},
		{
			name: "empty sender",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.Sender = ""
				return ib
			}(),
			expectError: true,
			errContains: "sender cannot be empty",
		},
		{
			name: "empty log_index",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.LogIndex = ""
				return ib
			}(),
			expectError: true,
			errContains: "log_index cannot be empty",
		},
		{
			name: "unspecified tx_type",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.TxType = types.TxType_UNSPECIFIED_TX
				return ib
			}(),
			expectError: true,
			errContains: "invalid tx_type",
		},
		{
			name: "invalid tx_type out of range",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.TxType = 99
				return ib
			}(),
			expectError: true,
			errContains: "invalid tx_type",
		},
		{
			name: "passes with extra payload on non-payload type (ignored at execution time)",
			inbound: func() types.Inbound {
				ib := validInbound
				ib.UniversalPayload = &types.UniversalPayload{Data: "0x1234"}
				return ib
			}(),
			expectError: false,
		},
	}
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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-65)
```go
	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
			// Store the revert failure reason on the UTX so it's queryable on-chain.
			// The FAILED PCTx is already recorded above — this adds why the revert wasn't attached.
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
				utx.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				// UpdateUniversalTx only fails on infra issues — return to roll back and retry
				return storeErr
			}
		}
	}
```
