### Title
Inbound funds become permanently unrecoverable when all ballot variants terminate as EXPIRED/REJECTED — no refund/escape-hatch flow exists - ([File: x/uexecutor/keeper/ballot_hooks.go])

### Summary
When a cross-chain deposit's `Inbound` ballot fails to reach quorum on any single payload variant (all variants end `EXPIRED` or `REJECTED`), `x/uexecutor` never creates a `UniversalTx`, never mints the corresponding PRC20, and moves the audit trail to `ExpiredInbounds`. The code and docs explicitly acknowledge that this collection is only "consumed by the **future** escape-hatch refund flow" — that flow does not exist yet. This is the direct analog of the external report: users' deposited funds sit unreachable in the TSS-controlled vault on the source chain with no on-chain path (self-service or even fully admin-driven, for the REJECTED case) to recover them.

### Finding Description
`VoteInbound` (`x/uexecutor/keeper/msg_vote_inbound.go:60-70`) derives a `ballotKey` from the raw `Inbound` bytes via `types.GetInboundBallotKey`, and each Universal Validator votes using its own locally-decoded `Inbound` payload. If validators produce byte-level differences in the decoded/marshaled `Inbound` (different formatting of otherwise-identical fields — a scenario the codebase itself calls out as the reason `InboundVariant`/`PendingInboundEntry` exist, see `proto/uexecutor/v1/pending.proto:11-23`), votes are split across multiple distinct ballots for the same logical deposit. None of them may reach the 2/3+1 threshold before their `BlockHeightExpiry`.

`BallotHooks.afterInboundBallotTerminal` (`x/uexecutor/keeper/ballot_hooks.go:72-143`) handles this: once **all** variants for a given `utx_key` are terminal and none `PASSED`, the entry is removed from `PendingInbounds` and copied into `ExpiredInbounds` (line 134-142). At no point is a `UniversalTx` created, a PRC20 minted, or any outbound/refund constructed for this path — no `UniversalTx.PcTx`/`OutboundTx` records exist to hang recovery logic off of.

The only theoretical remediation path, `MsgRevertStuckInbound` / `RevertStuckInbound` (`x/uexecutor/keeper/admin_revert.go:26-91`), is (a) admin-only (`signer must equal uvalidator Params.Admin`), and (b) strictly gated on `ballot.Status == BALLOT_STATUS_EXPIRED` (`admin_revert.go:47-51`). It explicitly rejects ballots whose terminal status is `BALLOT_STATUS_REJECTED` (`x/uvalidator/keeper/ballot.go:160-163` shows `REJECTED` is a valid terminal state distinct from `EXPIRED`). So even the admin-driven escape hatch cannot recover funds whose ballot variants terminated as `REJECTED` rather than `EXPIRED` — there is categorically no path, privileged or otherwise, to return those funds.

This mirrors the external report precisely: the report's `RewardsSystem.withdrawEarnings` only being callable in a narrow window between game phases is architecturally analogous to Push Chain's inbound funds only being recoverable if a single ballot variant happens to reach quorum before expiry; once that window closes (all variants terminal-failure), the documented "future" replacement mechanism doesn't exist, exactly as the report recommends replacing `RewardsSystem` with a system that guarantees fund availability. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
This is a permanent freezing of user-controlled cross-chain funds. A user deposits on the source chain (funds move into the TSS vault) and, purely due to honest-validator non-determinism in decoding/marshaling the observed event (no malicious actor required), the vote splits across multiple ballot variants, none reaching quorum before expiry. The result: no `UniversalTx`, no mint, no revert outbound, and the audit-trail entry sits in `ExpiredInbounds` with a mechanism ("future escape-hatch refund flow") that is explicitly not implemented. For `REJECTED` terminal outcomes even the admin path is hard-blocked by the `EXPIRED`-only check. This is High impact under the fund-freezing criteria in the allowed-impact gate.

### Likelihood Explanation
Likelihood is High for the "non-deterministic decode → multi-variant ballot" trigger, since the variant-tracking machinery (`InboundVariant`, `PendingInboundEntry.Variants`) was purpose-built by the team to handle exactly this real, observed condition — it is not a hypothetical. Reaching a state where *all* variants terminate as failures (rather than one passing) requires an unlucky/adversarial split of votes plus expiry, which is plausible under normal validator set churn, network partition, or slightly different observed logs, without any validator or admin needing to act maliciously.

### Recommendation
Implement the "future escape-hatch refund flow" referenced in `pending.proto`/README now rather than deferring it: when an `ExpiredInboundEntry` is created, either (a) automatically construct an `INBOUND_REVERT`-style outbound sourced from the original inbound's `Sender`/`Amount`/`AssetAddr` (reusing `buildRevertOutbound`) without requiring a UTX to have first existed, or (b) relax `RevertStuckInbound`'s precondition to also accept `ExpiredInbounds` entries regardless of whether their terminal status was `EXPIRED` or `REJECTED`, and drive the operation from `ExpiredInbounds` state directly instead of requiring the admin to resupply the exact original ballot-matching inbound bytes.

### Proof of Concept
1. Two Universal Validators (UV1, UV2) submit `MsgVoteInbound` for the same real source-chain deposit but with a byte-level formatting difference in the decoded `Inbound` (e.g., different `RawPayload`/case/whitespace in a string field) — an entirely honest, plausible occurrence given the documented rationale for `InboundVariant`.
2. This produces two distinct `ballotKey`s (`types.GetInboundBallotKey`), so two separate `Ballot`s are created, each below the 2/3+1 threshold.
3. Both ballots reach `BlockHeightExpiry` and are marked `EXPIRED` via `ExpireBallotsBeforeHeight` → `MarkBallotExpired` (`x/uvalidator/keeper/ballot.go:126-151`), or a further UV disagreement path results in `REJECTED`.
4. `BallotHooks.afterInboundBallotTerminal` sees both variants terminal-failure, removes the `PendingInbounds` entry, and writes it to `ExpiredInbounds` (`ballot_hooks.go:120-142`) — confirmed exactly by `TestBallotHook_MultiVariant_AllExpiredRoutesEntireEntry` in `test/integration/uexecutor/pending_inbound_audit_trail_test.go:293-316`.
5. No `UniversalTx` was ever created for this deposit. The user's funds, already locked in the TSS vault on the source chain, have no corresponding mint, outbound, or refund record on Push Chain.
6. If any variant's terminal status is `REJECTED` (not `EXPIRED`), even `MsgRevertStuckInbound` is unusable, since `RevertStuckInbound` hard-fails on any non-`EXPIRED` ballot status (`admin_revert.go:47-51`), leaving no recovery path whatsoever. [5](#0-4) [6](#0-5)

### Citations

**File:** x/uexecutor/keeper/ballot_hooks.go (L134-142)
```go
	// All variants are terminal-failure (EXPIRED or REJECTED). Preserve
	// the full audit trail in ExpiredInbounds for the future escape-hatch
	// refund flow.
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	return h.k.ExpiredInbounds.Set(ctx, utxKey, types.ExpiredInboundEntry{
		UtxKey:          utxKey,
		Variants:        entry.Variants,
		ExpiredAtHeight: uint64(sdkCtx.BlockHeight()),
	})
```

**File:** proto/uexecutor/v1/pending.proto (L67-79)
```text
// ExpiredInboundEntry preserves the full per-variant audit trail of an
// inbound that failed to reach quorum on any variant. Consumed by the
// future escape-hatch refund flow.
message ExpiredInboundEntry {
  option (gogoproto.equal) = true;

  string utx_key = 1;
  // Each variant carries its terminal_status (EXPIRED or REJECTED).
  repeated InboundVariant variants = 2 [(gogoproto.nullable) = false];
  // Block height when the entry was moved here (i.e. when the LAST
  // variant's ballot reached a terminal state).
  uint64 expired_at_height = 3;
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

**File:** x/uvalidator/keeper/ballot.go (L153-189)
```go
// MarkBallotFinalized moves a ballot from active to finalized (PASSED or REJECTED).
// Side-effect ordering matches MarkBallotExpired: secondary indexes are
// updated before the canonical ballot record is rewritten with its final status.
//
// Fires the BallotHooks terminal callback (if registered) AFTER all writes
// have committed. Hook errors are logged but do NOT block the terminal
// transition.
func (k Keeper) MarkBallotFinalized(ctx context.Context, id string, status types.BallotStatus) error {
	if status != types.BallotStatus_BALLOT_STATUS_PASSED && status != types.BallotStatus_BALLOT_STATUS_REJECTED {
		return fmt.Errorf("invalid finalization status: %v", status)
	}

	ballot, err := k.Ballots.Get(ctx, id)
	if err != nil {
		return err
	}

	k.Logger().Debug("marking ballot as finalized",
		"ballot_id", id,
		"final_status", status.String(),
	)

	if err := k.ActiveBallotIDs.Remove(ctx, id); err != nil {
		return err
	}
	if err := k.FinalizedBallotIDs.Set(ctx, id); err != nil {
		return err
	}

	ballot.Status = status
	if err := k.Ballots.Set(ctx, id, ballot); err != nil {
		return err
	}

	k.fireBallotTerminalHook(ctx, ballot.Id, ballot.BallotType, status)
	return nil
}
```

**File:** test/integration/uexecutor/pending_inbound_audit_trail_test.go (L293-316)
```go
func TestBallotHook_MultiVariant_AllExpiredRoutesEntireEntry(t *testing.T) {
	chainApp, ctx, _ := utils.SetAppWithValidators(t)

	inboundA := makeInbound("0xallexp", "0xsenderA")
	inboundB := makeInbound("0xallexp", "0xsenderB")
	utxKey := uexecutortypes.GetInboundUniversalTxKey(inboundA)

	ballotA := seedPendingBallot(t, chainApp, ctx, inboundA, auditVoter1)
	ballotB := seedPendingBallot(t, chainApp, ctx, inboundB, auditVoter2)

	require.NoError(t, chainApp.UvalidatorKeeper.MarkBallotExpired(ctx, ballotA))
	require.NoError(t, chainApp.UvalidatorKeeper.MarkBallotExpired(ctx, ballotB))

	hasPending, err := chainApp.UexecutorKeeper.PendingInbounds.Has(ctx, utxKey)
	require.NoError(t, err)
	require.False(t, hasPending, "entry must be removed once all variants are terminal")

	expired, err := chainApp.UexecutorKeeper.ExpiredInbounds.Get(ctx, utxKey)
	require.NoError(t, err)
	require.Len(t, expired.Variants, 2, "ExpiredInbounds preserves the full audit trail")
	for _, v := range expired.Variants {
		require.Equal(t, uvalidatortypes.BallotStatus_BALLOT_STATUS_EXPIRED, v.TerminalStatus)
	}
}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L60-70)
```go
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
```
