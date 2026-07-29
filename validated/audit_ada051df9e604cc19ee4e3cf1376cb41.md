### Title
Deterministic execution-path error at ballot finalization can permanently roll back a finalizing vote, blocking universal-tx creation and orphaning inbound funds - (File: x/uexecutor/keeper/msg_vote_inbound.go)

### Summary
In `Controller.sol`'s `triggerEndEpoch`, a downstream call (`getLatestPrice()`) that can deterministically revert (Arbitrum sequencer grace period) blocks an otherwise-ready state finalization, preventing winners from withdrawing. The Push Chain analog is in `VoteInbound`: the *finalizing* vote for an inbound ballot performs ballot commitment, UTX creation, and then dispatches execution (`k.ExecuteInbound`) all inside one message call. If `ExecuteInbound` returns a hard Go error instead of recording a graceful failure, the entire transaction — including the just-committed ballot vote and UTX creation — is rolled back by the Cosmos SDK's message-level state branching.

### Finding Description
`VoteInbound` records the validator's vote and, if it finalizes the ballot, immediately proceeds (still inside the same message call) to create the `UniversalTx`, remove the pending-inbound entry, validate for execution, and finally dispatch `k.ExecuteInbound(ctx, utx)`: [1](#0-0) 

Note that `commit()` (writing the ballot-vote cache into the outer `sdkCtx`) happens early, at line 75, but `sdkCtx` itself is still the branched context for this single Cosmos SDK message execution: [2](#0-1) 

If any later step — most critically `k.ExecuteInbound`, which dispatches to per-tx-type execution such as `ExecuteInboundFundsAndPayload`, `ExecuteInboundGas`, `ExecuteInboundGasAndPayload` — returns a non-nil error rather than a caught/handled failure, that error propagates all the way out of `VoteInbound` to the `MsgVoteInbound` message-server handler. In the Cosmos SDK, a message handler returning an error causes the entire enclosing transaction to be rejected and *all* state writes performed during that message's execution to be discarded — including the ballot-vote commit, the UTX creation, and the `RemovePendingInbound` call that happened earlier in the same call.

I confirmed that several execution sub-paths are explicitly hardened against this: the smart-contract branch of `ExecuteInboundFundsAndPayload` catches `contractErr`/`feeErr` and records them as a `FAILED` `PCTx` rather than returning an error: [3](#0-2) 
and the deposit-failure path builds a graceful `INBOUND_REVERT` outbound instead of bubbling an error: [4](#0-3) 

However, I was not able to fully verify every branch of `ExecuteInboundFunds`, `ExecuteInboundGas`, and `ExecuteInboundGasAndPayload` (only partial file contents were available in the index), so I cannot confirm that *all* code paths reachable from attacker-controlled inbound payload data (e.g., malformed `UniversalPayload`, adversarial ABI encoding, or unexpected token/registry state) are equally guarded against returning a hard error instead of a recorded failure.

### Impact Explanation
If a single deterministic hard-error path exists anywhere in the `ExecuteInbound` dispatch tree for attacker-influenced input, the consequence is severe and matches the External Report's class exactly:
- The finalizing validator's vote transaction always fails and is rolled back, so the ballot can never actually finalize — since the vote that would have triggered `PASSED` never durably commits.
- Because the ballot vote itself is rolled back (not just the execution step), the ballot may never persist far enough to reach `EXPIRED` status, which is the required precondition for the admin escape hatch `RevertStuckInbound`: [5](#0-4) 
- This can permanently strand user funds already deposited into the external gateway contract, with no `UniversalTx`, no `INBOUND_REVERT` outbound, and no admin-recoverable state — a stronger version of the original bug's "winners cannot withdraw" impact, potentially reaching irrecoverable fund loss rather than just delay.

### Likelihood Explanation
Medium/uncertain — contingent on the existence of an unguarded hard-error return path in `ExecuteInboundFunds`, `ExecuteInboundGas`, or `ExecuteInboundGasAndPayload` that is reachable with attacker-controlled but validator-agreed-upon inbound data (so the deterministic failure is reproduced by every UV, not just an infra hiccup). I confirmed the dangerous general architecture (finalization + UTX creation + execution dispatch sharing one atomic, all-or-nothing message context) and confirmed some execution paths are deliberately guarded, but I could not fully audit `ExecuteInboundFunds`/`ExecuteInboundGas`/`ExecuteInboundGasAndPayload` end-to-end in this pass.

### Recommendation
- Ensure `k.ExecuteInbound` (and everything it calls) never returns a raw Go error for cases driven by attacker/payload data; instead it should always convert such failures into a stored `FAILED` `PCTx` plus (for non-isCEA) a scheduled `INBOUND_REVERT` outbound, mirroring `handleFailedInboundValidation`.
- Alternatively, isolate ballot finalization/UTX creation from downstream execution by committing the UTX in one message-level unit of work and dispatching `ExecuteInbound` in a separately cache-wrapped context whose errors are always caught and recorded rather than propagated.
- Add fuzz/property tests that submit malformed `UniversalPayload` data through the full quorum-voting path to confirm no input can cause a hard error at the finalizing vote.

### Proof of Concept
Conceptual (not fully verified end-to-end due to index limits on `execute_inbound_gas.go` / `execute_inbound_gas_and_payload.go` / `execute_inbound_funds.go`):
1. Attacker submits an inbound deposit on the source chain with a crafted `UniversalPayload` or asset/registry state that is accepted by `ValidateForExecution` but causes a hard (non-recovered) error deep in the corresponding `ExecuteInbound*` function.
2. UVs vote `MsgVoteInbound` with identical (honest) observations of this data, reaching quorum.
3. The finalizing UV's transaction reaches `ExecuteInbound`, hits the hard error, and the entire transaction (ballot vote + UTX creation + pending removal) is rolled back.
4. Every subsequent attempt by any UV to cast the same finalizing vote reproduces the identical failure (data is deterministic), so the ballot never reaches `PASSED` or accumulates a persisted `EXPIRED` state, and `RevertStuckInbound` cannot apply since `GetBallot` finds nothing.
5. Funds already locked in the source-chain gateway are permanently unrecoverable.

Given the incomplete visibility into all `ExecuteInbound*` branches, this should be verified by a full audit/session covering `x/uexecutor/keeper/execute_inbound_funds.go`, `execute_inbound_gas.go`, and `execute_inbound_gas_and_payload.go` in their entirety to locate (or rule out) an unguarded `return err` reachable from attacker-controlled inbound data.

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L54-76)
```go
	// use a temporary context to not commit any ballot state change in case of error
	tmpCtx, commit := sdkCtx.CacheContext()

	// Step 2: Record this validator's vote in the per-utx PendingInbounds entry
	// (variant-aware audit trail). Each unique Inbound payload becomes its own
	// variant; multiple variants per utx_key indicate validator divergence.
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
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}

	commit()

```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L148-157)
```go
	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}

	return nil
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L187-206)
```go
	// If deposit failed, stop here.
	if execErr != nil {
		if shouldRevert {
			revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)
			if attachErr := k.attachOutboundsToUtx(
				sdkCtx,
				universalTxKey,
				[]*types.OutboundTx{revertOutbound},
				revertReason,
			); attachErr != nil {
				if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
					u.RevertError = attachErr.Error()
					return nil
				}); storeErr != nil {
					return storeErr
				}
			}
		}
		return nil
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L259-282)
```go
		callPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		if contractReceipt != nil {
			callPcTx.TxHash = contractReceipt.Hash
			callPcTx.GasUsed = contractReceipt.GasUsed
		}
		switch {
		case contractErr != nil:
			callPcTx.ErrorMsg = contractErr.Error()
		case feeErr != nil:
			callPcTx.ErrorMsg = fmt.Sprintf("gas fee deduction failed: %s", feeErr.Error())
		default:
			callPcTx.Status = "SUCCESS"
		}
		if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &callPcTx)
			return nil
		}); updateErr != nil {
			return updateErr
		}
		return nil
```

**File:** x/uexecutor/keeper/admin_revert.go (L42-51)
```go
	ballot, err := k.uvalidatorKeeper.GetBallot(ctx, ballotKey)
	if err != nil {
		return "", "", errors.Wrap(sdkErrors.ErrNotFound, fmt.Sprintf("ballot for inbound not found (key=%s): %s", ballotKey, err))
	}

	if ballot.Status != uvalidatortypes.BallotStatus_BALLOT_STATUS_EXPIRED {
		return "", "", errors.Wrap(sdkErrors.ErrInvalidRequest,
			fmt.Sprintf("ballot %s status is %s; admin revert requires EXPIRED (use MsgRecomputeBallotQuorum to drive a stuck pending ballot to EXPIRED)",
				ballotKey, ballot.Status.String()))
	}
```
