### Title
Inbounds with `isCEA=true` on a non-CEA-eligible `TxType` fail validation with no automatic revert path, permanently stranding already-bridged funds - (File: `x/uexecutor/types/inbound.go`, `x/uexecutor/keeper/handle_failed_inbound_validation.go`)

### Summary
An inbound whose source-chain observation sets `IsCEA=true` together with a `TxType` outside the `{FUNDS, FUNDS_AND_PAYLOAD, GAS_AND_PAYLOAD}` allowlist (e.g. plain `TxType_GAS`) fails `ValidateForExecution` on the `isCEA` guard before any deposit/mint or amount check runs. Because the record is `IsCEA=true`, `handleFailedInboundValidation` deliberately skips creating the `INBOUND_REVERT` outbound that would otherwise return the user's funds to the source chain. The result is a `UniversalTx` permanently stuck with only a `FAILED` `PCTx` and no outbound — mirroring the Starklane `use_withdraw_auto` bug where an unsupported option combination causes a hard revert with no way to reclaim already-committed funds.

### Finding Description
`ValidateForExecution` in [1](#0-0)  rejects any inbound where `IsCEA` is true but `TxType` is not `FUNDS`, `FUNDS_AND_PAYLOAD`, or `GAS_AND_PAYLOAD`. This check runs unconditionally, before the per-`TxType` recipient/payload checks, so it triggers even for `TxType_GAS` (a pure gas-top-up inbound) whose underlying source-chain funds were already locked/consumed by the gateway before the vote was ever cast.

When this validation fails, `VoteInbound` calls `handleFailedInboundValidation` at [2](#0-1) . That function always records a `FAILED` `PCTx` on the UTX, but only schedules an `INBOUND_REVERT` outbound "for non-isCEA inbounds": [3](#0-2) 

Since the triggering condition is precisely `IsCEA == true`, the revert branch is always skipped for exactly the failure class this bug produces. The comment even acknowledges the exclusion is intentional/by design ("isCEA failures never create an INBOUND_REVERT outbound"), but it does not distinguish between an isCEA failure that is a legitimate CEA-recipient issue (where a smart-contract recipient might have its own recovery path) and this isCEA/TxType-mismatch failure, which has no recipient-side recovery at all because the deposit never reached the recipient.

The only recorded on-chain artifact is a `UniversalTx` with a `FAILED` `PcTx[0]` and empty `OutboundTx`. There is no automatic mechanism to revisit this UTX. The module's `README.md` documents that `PendingInbounds`/`ExpiredInbounds` escape-hatch refund flow only applies when ballots reach `EXPIRED`/`REJECTED` [4](#0-3)  — but here the ballot `PASSED` (finalized) and a UTX was created, so this path is never entered. The only remaining recourse is the admin/governance-driven `RESCUE_FUNDS` `TxType`, which is a privileged action, not something the unprivileged depositor can trigger themselves.

### Impact Explanation
The user's real, already-bridged asset on the source chain is permanently unreachable through any unprivileged, automatic flow. This matches the "in scope" impact category of permanent loss/freezing of user funds in the universal execution/finalization flow. Unlike the zero-amount case which explicitly produces a revert outbound (`test/integration/uexecutor/inbound_zero_amount_test.go:498-499`), the isCEA/TxType-mismatch case has no equivalent safety net for ordinary user error or a maliciously/incorrectly formed source-chain event that a user submits themselves (analogous to a user mistakenly or deliberately setting `use_withdraw_auto=true` on Starknet).

### Likelihood Explanation
Triggering requires only that the user's own deposit transaction on the source-chain gateway encode `IsCEA=true` with an ineligible `TxType` such as `GAS` — this is fully attacker/user-controlled input reaching honest validators, who will faithfully observe and vote the inbound as submitted, and honest nodes will faithfully apply the described logic. No malicious validator, TSS, or admin action is required.

### Recommendation
Either (a) validate and reject the `isCEA`/`TxType` combination at the point the source-chain gateway/UEA payload is constructed so this state can never be observed and voted on, or (b) treat this specific failure mode (isCEA true on a TxType where isCEA is structurally unsupported, with an already-consumed source-chain deposit) as revert-eligible in `handleFailedInboundValidation`, scheduling an `INBOUND_REVERT` outbound the same way non-isCEA failures do, rather than relying solely on the privileged `RESCUE_FUNDS` path.

### Proof of Concept
1. On the source chain, a user calls the gateway with `TxType = GAS` (gas top-up) and sets the CEA flag to `true` in the emitted event (either the gateway UI allows this, or the user crafts the calldata directly if the gateway does not itself reject the combination).
2. Universal Validators observe the event faithfully and submit `MsgVoteInbound` with `Inbound{TxType: TxType_GAS, IsCEA: true, ...}`.
3. Once 2/3+ votes finalize the ballot, `VoteInbound` creates the `UniversalTx` and calls `inbound.ValidateForExecution()`, which returns the error at [5](#0-4) .
4. `handleFailedInboundValidation` records a `FAILED` `PcTx` and, because `inbound.IsCEA == true`, skips the revert-outbound branch entirely.
5. The `UniversalTx` remains forever in this state: `PcTx[0].Status == "FAILED"`, `OutboundTx` empty — no PRC20 was minted on Push Chain, and the source-chain funds are never returned, with no unprivileged path to recover them.

### Citations

**File:** x/uexecutor/types/inbound.go (L145-148)
```go
	// isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD
	if p.IsCEA && p.TxType != TxType_FUNDS && p.TxType != TxType_FUNDS_AND_PAYLOAD && p.TxType != TxType_GAS_AND_PAYLOAD {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD tx types, got: %v", p.TxType)
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L136-146)
```go
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}
```

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-47)
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
```

**File:** x/uexecutor/README.md (L254-258)
```markdown
- **Removed** when ALL related ballot variants reach a terminal state. If any
  variant ended `PASSED`, the existing post-finalization path in `VoteInbound`
  produced a `UniversalTx`. If ALL variants ended `EXPIRED`/`REJECTED`, the
  full per-variant audit trail is moved to `ExpiredInbounds` for the future
  escape-hatch refund flow.
```
