## Finding

### Title
Unvalidated `RevertInstructions.FundRecipient` format lets refunds permanently freeze in `buildRevertOutbound` - (File: `x/uexecutor/keeper/build_revert_outbound.go`)

### Summary
`buildRevertOutbound` copies `inbound.RevertInstructions.FundRecipient` verbatim into the `INBOUND_REVERT` outbound's `Recipient` field without ever checking that the address format matches what `inbound.SourceChain` (the refund's `DestinationChain`) actually requires. Neither `Inbound.Canonicalize()`/`ValidateBasic()` nor `OutboundTx.ValidateBasic()` enforce this, so a malformed/format-mismatched `FundRecipient` sails through and is written into on-chain state as a `Status_PENDING` outbound before the destination-chain-specific address parsing ever runs.

### Finding Description
`buildRevertOutbound` sets the revert recipient directly from attacker-controllable input: [1](#0-0) 

The only normalization applied is `Inbound.Canonicalize()`, which is explicitly documented as *lenient* — "unparseable values are kept trimmed, never rejected": [2](#0-1) 

`Inbound.ValidateBasic()` validates `source_chain`, `tx_hash`, `sender`, `log_index`, and `tx_type` only — it never inspects `RevertInstructions.FundRecipient`: [3](#0-2) 

`OutboundTx.ValidateBasic()` likewise only requires `Recipient` to be non-empty (it validates `Sender` as a hex address, but not `Recipient` against `DestinationChain`'s expected format): [4](#0-3) 

This unvalidated field is then attached with `Status_PENDING` from every revert-producing call site (`execute_inbound_funds.go`, `handle_failed_inbound_validation.go`, `admin_revert.go`), entering `PendingOutbounds` for TSS signing: [5](#0-4) 

The mismatch is only discovered much later, in the `universalClient` TSS signing pipeline, when the destination-chain `TxBuilder` tries to parse the recipient into its native address type. For Solana destinations this fails hard: [6](#0-5) [7](#0-6) 

By the time this error surfaces (inside `coordinator.buildSignTransaction`), the corresponding `OutboundTx` already exists on-chain as `Status_PENDING`: [8](#0-7) 

There is no code path found in the scoped `x/` keeper that detects this signing-time parse failure and transitions the outbound to `FAILED`/`ABORTED` or otherwise re-derives a valid recipient — the admin `RevertStuckInbound` recovery path is also blocked once the `UniversalTx` already exists (`admin_revert.go` rejects a second attempt if the UTX key is already present).

### Impact Explanation
An inbound deposit whose `SourceChain` requires one address format (e.g., a Solana CAIP-2 id) but whose `RevertInstructions.FundRecipient` is set to a value that cannot be parsed as that chain's native address (not a valid base58 Solana pubkey and not a 32-byte hex blob) results in a revert/refund outbound that can never be completed by the TSS/coordinator pipeline. The user's already-deposited/minted funds are earmarked for a refund that is permanently unresolvable through the normal protocol flow, and the existing UTX record blocks any straightforward re-issuance of the revert outbound. This is a permanent-freezing-of-user-funds condition, not merely a display or gas issue.

### Likelihood Explanation
The `RevertInstructions.FundRecipient` value originates from data the depositing user supplies when initiating the cross-chain deposit (relayed as-is by honest validators via `MsgVoteInbound`); it requires no validator, TSS, or admin misbehavior — only an ordinary user submitting a badly formatted (or deliberately format-mismatched) `FundRecipient`. Given no scoped validation exists at vote time, execution time, or outbound-attachment time, this is trivially reachable.

### Recommendation
Add chain-format-aware validation of `RevertInstructions.FundRecipient` against `inbound.SourceChain` (mirroring the strict address parsing already performed by each chain's `TxBuilder`) either in `Inbound.ValidateForExecution` or in `buildRevertOutbound` before the outbound is created/attached as `PENDING`. If the address cannot be validated for the destination chain's format, fall back to `inbound.Sender` (which is already canonicalized/validated for that chain) instead of trusting the unchecked `FundRecipient`, and surface a `FAILED` PCTx rather than creating an unsignable `PENDING` outbound.

### Proof of Concept
1. Attacker deposits on a Solana source chain (`SourceChain = "solana:<cluster>"`), submitting an inbound event (relayed faithfully by honest validators) with `RevertInstructions.FundRecipient` set to a value that is neither a valid base58 Solana pubkey nor a 32-byte hex string (e.g., a standard EVM `0x...` 20-byte address).
2. Trigger a revert (e.g., token-config missing, causing `ExecuteInboundFunds`/`handleFailedInboundValidation` to call `buildRevertOutbound`).
3. Observe the resulting `OutboundTx` (`TxType_INBOUND_REVERT`) is attached with `OutboundStatus = Status_PENDING`, `Recipient = FundRecipient` unchanged (per `TestRevertStuckInbound_HappyPath_ExpiredBallot_CreatesRevertOutbound` behavior confirming `Recipient` equals `RevertInstructions.FundRecipient` verbatim).
4. When the coordinator attempts `GetOutboundSigningRequest`/`BuildOutboundTransaction` for the Solana destination, `solana.PublicKeyFromBase58(data.Recipient)` fails and the hex fallback also fails (wrong byte length), returning `"invalid recipient address format (expected Solana Pubkey)"`.
5. No scoped code path transitions this outbound out of `PENDING`; the refund is stuck indefinitely with no automatic recovery, and the pre-existing `UniversalTx` blocks the `RevertStuckInbound` admin path from reissuing it cleanly.

### Citations

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-14)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}
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

**File:** x/uexecutor/types/inbound.go (L84-121)
```go
// ValidateBasic does minimal sanity checks needed to accept a vote.
// Only fields required to identify the inbound and create a UTX key are validated here.
// Execution-level validation (amount, addresses, payload, recipient) is deferred to
// ValidateForExecution so that invalid inbounds still produce an on-chain UTX record
// (with a failed PCTx / revert) instead of silently dropping the vote and leaving
// user funds stuck in the gateway.
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

**File:** x/uexecutor/types/outbound_tx.go (L23-45)
```go
// ValidateBasic does the sanity check on the OutboundTx fields.
func (p OutboundTx) ValidateBasic() error {
	// Validate destination_chain (must follow CAIP-2 format)
	chain := strings.TrimSpace(p.DestinationChain)
	if chain == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "destination_chain cannot be empty")
	}
	if !strings.Contains(chain, ":") {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "destination_chain must be in CAIP-2 format <namespace>:<reference>")
	}

	// recipient must not be empty
	if strings.TrimSpace(p.Recipient) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
	}

	// sender
	if strings.TrimSpace(p.Sender) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "sender cannot be empty")
	}
	if !utils.IsValidAddress(p.Sender, utils.HEX) {
		return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address: %s", p.Sender)
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L74-86)
```go
	// isCEA failures never create an INBOUND_REVERT outbound
	// (consistent with execute_inbound_funds_and_payload.go and execute_inbound_gas_and_payload.go)
	if err != nil && !inbound.IsCEA {
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
		if attachErr := k.attachOutboundsToUtx(sdkCtx, utx.Id, []*types.OutboundTx{revertOutbound}, err.Error()); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, utx.Id, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** universalClient/chains/svm/tx_builder.go (L298-311)
```go
	// recipient/target: Solana pubkey of the destination. Used differently depending on instruction:
	//   - Withdraw (id=1): the wallet that receives the funds (target = recipient)
	//   - Execute (id=2): the target program to CPI into (target = destination_program)
	//   - Revert (id=3,4): the wallet that gets the refund
	var recipientPubkey solana.PublicKey
	recipientPubkey, err = solana.PublicKeyFromBase58(data.Recipient)
	if err != nil {
		hexBytes, hexErr := hex.DecodeString(removeHexPrefix(data.Recipient))
		if hexErr != nil || len(hexBytes) != 32 {
			return nil, fmt.Errorf("invalid recipient address format (expected Solana Pubkey): %s", data.Recipient)
		}
		recipientPubkey = solana.PublicKeyFromBytes(hexBytes)
	}

```

**File:** universalClient/chains/svm/tx_builder.go (L770-777)
```go
	recipientPubkey, err := solana.PublicKeyFromBase58(data.Recipient)
	if err != nil {
		hexBytes, hexErr := hex.DecodeString(removeHexPrefix(data.Recipient))
		if hexErr != nil || len(hexBytes) != 32 {
			return nil, 0, fmt.Errorf("invalid recipient address format: %s", data.Recipient)
		}
		recipientPubkey = solana.PublicKeyFromBytes(hexBytes)
	}
```

**File:** universalClient/tss/coordinator/coordinator.go (L775-782)
```go
	// Get the signing request (nonce is required for SIGN)
	if assignedNonce == nil {
		return nil, fmt.Errorf("assigned nonce is required for sign transaction")
	}
	signingReq, err := builder.GetOutboundSigningRequest(ctx, &data, *assignedNonce)
	if err != nil {
		return nil, fmt.Errorf("failed to get outbound signing request: %w", err)
	}
```
