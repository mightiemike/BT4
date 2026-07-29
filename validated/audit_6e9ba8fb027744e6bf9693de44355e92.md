## Analysis: Analog Found

The Beanstalk pattern — user funds transferred into the protocol during order creation, but never reflected in the internal balance ledger that the migration process actually copies, causing permanent loss — maps directly onto Push Chain's inbound ballot lifecycle in `x/uexecutor`. When an inbound fails to reach quorum, the user's funds have already left the source chain (transferred into the gateway/vault) but no `UniversalTx`/PRC20 mint is ever created, so there is no "internal balance" recording the deposit. The chain's only recovery mechanism, `RevertStuckInbound`, structurally excludes one of the two possible terminal-failure states.

### Title
Admin escape-hatch `RevertStuckInbound` only accepts `EXPIRED` ballots, permanently stranding user funds whose inbound ballot terminalizes as `REJECTED` - (File: `x/uexecutor/keeper/admin_revert.go`)

### Summary
`x/uexecutor` tracks inbound votes as per-variant ballots. When ALL variants of a logical inbound reach a terminal failure state (`EXPIRED` or `REJECTED`), the audit trail is moved to `ExpiredInbounds` "for the future escape-hatch refund flow" [1](#0-0) . The only implemented refund mechanism, `RevertStuckInbound`, hard-requires the ballot status to be exactly `EXPIRED` and rejects everything else, including `REJECTED` [2](#0-1) .

### Finding Description
An inbound's ballot key is derived from the exact marshaled bytes of the `Inbound` payload submitted by each validator [3](#0-2) . When honest validators submit byte-level-different payloads for the same logical source-chain event (differing decoded fields/formatting for ambiguous or edge-case `raw_payload` data the user controls when constructing the source-chain transaction), multiple independent ballots ("variants") are created for the same `utx_key` [4](#0-3) . The generic ballot machine in `x/uvalidator` finalizes a ballot as `REJECTED` once enough votes make its supermajority threshold mathematically unreachable — this occurs purely from honest vote-splitting across variants, requiring no malicious validator behavior.

`BallotHooks.afterInboundBallotTerminal` treats `EXPIRED` and `REJECTED` identically: once all variants are terminal and none `PASSED`, the entry is moved into `ExpiredInbounds`, explicitly preserving both terminal statuses [5](#0-4) , confirmed by the integration test `TestBallotHook_SingleVariantRejectedRoutesToExpiredInbounds` which exercises exactly this `REJECTED` path [6](#0-5) .

However, `Keeper.RevertStuckInbound` — the only code path capable of creating the refund `UniversalTx`/`INBOUND_REVERT` outbound — enforces a strict precondition that the underlying ballot's `Status` must equal `BALLOT_STATUS_EXPIRED`, returning an error for any other status (including `REJECTED`) [2](#0-1) . No other message, keeper function, or automated process in `x/uexecutor` creates a `UniversalTx`/refund outbound for an inbound whose ballot(s) settled as `REJECTED`.

Consequently, exactly as in the Beanstalk report — where Beans were transferred out of the user but never reflected in the internal balance that the migration contract could restore — here the user's asset has already left the source chain into the gateway/vault, but the corresponding `UniversalTx` (Push Chain's internal accounting record) is never created, and the one designed recovery path is unreachable for this terminal state.

### Impact Explanation
Funds deposited by a user on an external chain, whose inbound observation splits across validator-submitted variants that all end up `REJECTED`, become permanently unrecoverable: no PRC20 is minted, no `UniversalTx` exists, and the admin refund path categorically rejects the request. This is a permanent, protocol-level loss of user-controlled funds reachable without any privileged or malicious-validator action.

### Likelihood Explanation
Triggering the divergence requires only that the external gateway event/payload a user submits be parsed inconsistently by honest validators (e.g., boundary/malformed `raw_payload` fields), which is squarely within an unprivileged attacker's or even an unlucky ordinary user's control, since they fully control the source-chain transaction data. No validator collusion is required — the ballot machine's normal supermajority-unreachable logic produces `REJECTED` from honest votes alone.

### Recommendation
Extend `RevertStuckInbound` (or add an equivalent keeper path) to accept ballots whose terminal status is `REJECTED` in addition to `EXPIRED`, mirroring the `ExpiredInbounds` design intent that both terminal-failure statuses are meant to be "consumed by the future escape-hatch refund flow." At minimum, ensure `ExpiredInboundEntry` records with `REJECTED` variants have a working, tested refund path before considering the escape hatch complete.

### Proof of Concept
1. Craft a source-chain transaction whose gateway event data is ambiguous enough that honest Universal Validators canonicalize/decode the `Inbound` into two or more distinct byte-level variants for the same `utx_key`.
2. Validator votes split across variants such that no single variant can reach the 2/3 threshold before all remaining voters have voted — `x/uvalidator`'s ballot machine finalizes each variant's ballot as `BALLOT_STATUS_REJECTED`.
3. `BallotHooks.afterInboundBallotTerminal` observes all variants terminal, none `PASSED`, and writes the entry to `ExpiredInbounds` [7](#0-6) .
4. Admin (or anyone monitoring) attempts `MsgRevertStuckInbound` with the original inbound payload to refund the user; `RevertStuckInbound` fetches the ballot, finds `Status == BALLOT_STATUS_REJECTED`, and returns the error `"ballot status is REJECTED; admin revert requires EXPIRED"` [2](#0-1) .
5. No `UniversalTx` is ever created, no refund outbound is ever built, and the user's funds — already locked in the external-chain vault — remain permanently unrecoverable.

### Citations

**File:** x/uexecutor/keeper/ballot_hooks.go (L128-143)
```go
	for _, v := range entry.Variants {
		if v.TerminalStatus == uvalidatortypes.BallotStatus_BALLOT_STATUS_PASSED {
			return nil
		}
	}

	// All variants are terminal-failure (EXPIRED or REJECTED). Preserve
	// the full audit trail in ExpiredInbounds for the future escape-hatch
	// refund flow.
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	return h.k.ExpiredInbounds.Set(ctx, utxKey, types.ExpiredInboundEntry{
		UtxKey:          utxKey,
		Variants:        entry.Variants,
		ExpiredAtHeight: uint64(sdkCtx.BlockHeight()),
	})
}
```

**File:** x/uexecutor/keeper/admin_revert.go (L47-51)
```go
	if ballot.Status != uvalidatortypes.BallotStatus_BALLOT_STATUS_EXPIRED {
		return "", "", errors.Wrap(sdkErrors.ErrInvalidRequest,
			fmt.Sprintf("ballot %s status is %s; admin revert requires EXPIRED (use MsgRecomputeBallotQuorum to drive a stuck pending ballot to EXPIRED)",
				ballotKey, ballot.Status.String()))
	}
```

**File:** proto/uexecutor/v1/pending.proto (L25-47)
```text
// InboundVariant captures one Inbound payload variant submitted by one
// or more validators against a single logical inbound event (identified
// by the UTX key = sha256(source_chain:tx_hash:log_index)). Multiple
// variants may exist for the same UTX key when validators marshal
// slightly different bytes for the same logical event.
message InboundVariant {
  option (gogoproto.equal) = true;

  // ballot_id == hex(marshal(Inbound)) — the ballot key used by uvalidator.
  string ballot_id = 1;
  // The full Inbound payload exactly as voted (the bytes that produced
  // this ballot_id).
  Inbound inbound = 2;
  // Validator addresses (bech32) that voted on this exact variant.
  repeated string voters = 3;
  // Block height of the first vote on this variant.
  uint64 first_voted_at_height = 4;
  // Block height of the most recent vote on this variant.
  uint64 last_voted_at_height = 5;
  // Terminal status of this variant's ballot. PENDING while in-flight.
  // Populated by the uvalidator BallotHooks terminal callback.
  uvalidator.v1.BallotStatus terminal_status = 6;
}
```

**File:** x/uexecutor/README.md (L250-253)
```markdown
- **Variant-aware:** when validators marshal slightly different `Inbound` bytes
  for the same logical event (different decoded fields, formatting, etc.), each
  unique payload becomes its own `InboundVariant` inside the entry, with its
  own `ballot_id`, `voters[]`, and `terminal_status`.
```

**File:** test/integration/uexecutor/pending_inbound_audit_trail_test.go (L219-236)
```go
func TestBallotHook_SingleVariantRejectedRoutesToExpiredInbounds(t *testing.T) {
	chainApp, ctx, _ := utils.SetAppWithValidators(t)
	inbound := makeInbound("0xtoreject", "0xsender")
	utxKey := uexecutortypes.GetInboundUniversalTxKey(inbound)

	ballotKey := seedPendingBallot(t, chainApp, ctx, inbound, auditVoter1)

	require.NoError(t, chainApp.UvalidatorKeeper.MarkBallotFinalized(ctx, ballotKey, uvalidatortypes.BallotStatus_BALLOT_STATUS_REJECTED))

	has, err := chainApp.UexecutorKeeper.PendingInbounds.Has(ctx, utxKey)
	require.NoError(t, err)
	require.False(t, has)

	expired, err := chainApp.UexecutorKeeper.ExpiredInbounds.Get(ctx, utxKey)
	require.NoError(t, err)
	require.Len(t, expired.Variants, 1)
	require.Equal(t, uvalidatortypes.BallotStatus_BALLOT_STATUS_REJECTED, expired.Variants[0].TerminalStatus)
}
```
