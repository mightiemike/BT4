No length cap exists on `Inbound.RawPayload` (or `UniversalPayload.Data`) anywhere I could find in `x/uexecutor/types/inbound.go`, `msg_vote_inbound.go`, or `universal_payload.go` — validation there only checks hex-decodability and numeric string formats, never a maximum byte length.

### Title
Unbounded attacker-controlled `RawPayload` stored verbatim in `PendingInboundEntry`/`UniversalTx` risks oversized on-chain objects and per-UTX state bloat - (File: `x/uexecutor/keeper/inbound.go`)

### Summary
The BigVector report's root cause was an unbounded/oversized leaf object created from an unvalidated size parameter. The nearest Push Chain analog is that `Inbound.RawPayload` — the raw hex-encoded event data an attacker fully controls via their source-chain gateway transaction — is never length-checked before being persisted verbatim inside `InboundVariant.Inbound` (part of `PendingInboundEntry`, a single `collections.Map` value) and later inside the permanent `UniversalTx` record.

### Finding Description
`Inbound.ValidateBasic()`/`MsgVoteInbound.ValidateBasic()` ( [1](#0-0) ) never bounds the size of `RawPayload` or `UniversalPayload.Data`. `RecordInboundVote` stores the full `Inbound` (including `RawPayload`) inside a new `InboundVariant` appended to `PendingInboundEntry.Variants`, itself a single value under `collections.Map[string, types.PendingInboundEntry]` ( [2](#0-1) ). The ballot key derivation hashes `RawPayload` via `CanonicalizeHexBlob` ( [3](#0-2) ) so length has no bearing on ballot identity/dedup — an attacker can make a single logical inbound carry an arbitrarily large payload (bounded only by the source chain's own tx/calldata limits, which on EVM chains can be tens of KB) and it will be voted on, tallied, and once finalized, permanently embedded in `UniversalTx.InboundTx`, a record explicitly documented as living "forever" and "never deleted" ( [4](#0-3) ).

### Impact Explanation
Unlike Sui Move's hard 256,000-byte object limit, Cosmos SDK / IAVL does not hard-fail on large values, so this does not brick the module outright the way the original BigVector bug did. The impact here is more limited: growth of a single `PendingInboundEntry`/`UniversalTx` value driven entirely by attacker-controlled source-chain data, with no on-chain size ceiling, which can inflate gas costs of marshal/unmarshal on every touch of that entry, bloat state size/snapshot and gRPC query payloads, and degrade indexers/explorers that read `UniversalTx` back. This is a materiality/DoS-adjacent concern rather than a fund-loss or consensus-divergence bug, and is capped by the source chain's own transaction size limits, which somewhat limits severity.

### Likelihood Explanation
High reachability, low severity: any unprivileged user can trigger this simply by emitting a gateway event with maximal calldata on a supported source chain; no privileged action is required, and it depends only on honest UVs faithfully relaying what they observed (no malicious validator assumption needed). The magnitude of the bloat is bounded by the connected chain's own constraints (EVM block gas limit / calldata cost, or Solana's ~1232-byte tx limit as referenced in `universalClient/chains/svm/tx_builder.go`), which meaningfully limits how large a single payload can realistically get.

### Recommendation
Add an explicit maximum length check (e.g., a few KB, matching whatever the largest legitimate `UniversalPayload.Data` your supported chains would ever need) for `RawPayload` in `Inbound.ValidateBasic()`/`MsgVoteInbound.ValidateBasic()`, rejecting votes whose payload exceeds it before they reach `RecordInboundVote` or get embedded into `UniversalTx`.

### Proof of Concept
Not verified end-to-end (no terminal access in this session); reasoning is based on static review of `x/uexecutor/types/msg_vote_inbound.go`, `x/uexecutor/types/universal_payload.go`, `x/uexecutor/keeper/inbound.go`, and `x/uexecutor/types/keys.go`, none of which impose a byte-length ceiling on `RawPayload`/`UniversalPayload.Data`. A concrete PoC would require constructing a source-chain gateway transaction with maximal calldata, having it voted via `MsgVoteInbound`, and confirming the resulting `PendingInboundEntry`/`UniversalTx` size in a running devnet — recommend a Devin session with full repo/terminal access to build and run this reproduction.

**Confidence caveat:** Given Cosmos SDK's lack of a Sui-style hard object-size ceiling, this is a much weaker analog than the original report (no clean "runtime throws / feature bricked" outcome was confirmed) — flagged as a plausible but lower-confidence match to the requested bug class rather than a definitive vulnerability.

### Citations

**File:** x/uexecutor/types/msg_vote_inbound.go (L52-60)
```go
// ValidateBasic does a sanity check on the provided data.
func (msg *MsgVoteInbound) ValidateBasic() error {
	// validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	return msg.Inbound.ValidateBasic()
}
```

**File:** x/uexecutor/keeper/inbound.go (L27-64)
```go
func (k Keeper) RecordInboundVote(
	ctx context.Context,
	inbound types.Inbound,
	voter string,
	ballotID string,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	height := uint64(sdkCtx.BlockHeight())
	utxKey := types.GetInboundUniversalTxKey(inbound)

	entry, err := k.PendingInbounds.Get(ctx, utxKey)
	if err != nil && !errors.Is(err, collections.ErrNotFound) {
		return err
	}
	if errors.Is(err, collections.ErrNotFound) {
		entry = types.PendingInboundEntry{
			UtxKey:          utxKey,
			CreatedAtHeight: height,
		}
	}

	// Find or create the variant for this ballot.
	variantIdx := -1
	for i, v := range entry.Variants {
		if v.BallotId == ballotID {
			variantIdx = i
			break
		}
	}
	if variantIdx < 0 {
		entry.Variants = append(entry.Variants, types.InboundVariant{
			BallotId:           ballotID,
			Inbound:            &inbound,
			Voters:             []string{voter},
			FirstVotedAtHeight: height,
			LastVotedAtHeight:  height,
			TerminalStatus:     uvalidatortypes.BallotStatus_BALLOT_STATUS_PENDING,
		})
```

**File:** x/uexecutor/types/keys.go (L99-125)
```go
func GetInboundBallotKey(inbound Inbound) (string, error) {
	chain := strings.TrimSpace(inbound.SourceChain)

	// nil RevertInstructions and an empty FundRecipient are semantically
	// identical (revert falls back to sender) — digest them identically.
	fundRecipient := ""
	if inbound.RevertInstructions != nil {
		fundRecipient = utils.LenientCanonicalizeAddress(chain, inbound.RevertInstructions.FundRecipient)
	}

	return hashFields(
		InboundBallotDomain,
		chain,
		utils.LenientCanonicalizeTxHash(chain, inbound.TxHash),
		strings.TrimSpace(inbound.LogIndex),
		utils.LenientCanonicalizeAddress(chain, inbound.Sender),
		// Recipient lives on Push Chain (EVM) regardless of source chain.
		utils.LenientCanonicalizeEVMAddress(inbound.Recipient),
		strings.TrimSpace(inbound.Amount),
		utils.LenientCanonicalizeAddress(chain, inbound.AssetAddr),
		fmt.Sprintf("%d", inbound.TxType),
		utils.CanonicalizeHexBlob(inbound.VerificationData),
		fundRecipient,
		fmt.Sprintf("%t", inbound.IsCEA),
		utils.CanonicalizeHexBlob(inbound.RawPayload),
		// universal_payload intentionally excluded (derived, ignored on-chain).
	), nil
```

**File:** x/uexecutor/README.md (L25-27)
```markdown
## The `UniversalTx` Record

`UniversalTx` (UTX) is the canonical, end-to-end record of a single crosschain transaction as it travels through Push Chain. One UTX is created per observed inbound and lives forever (it is never deleted, only mutated as new pieces of evidence arrive). It is the only object in the module that the rest of the protocol — Universal Validators, the JSON-RPC layer, indexers, the explorer — needs to read in order to know what's happening with a given crosschain action.
```
