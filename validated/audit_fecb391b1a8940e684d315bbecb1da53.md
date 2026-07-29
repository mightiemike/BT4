### Title
Missing zero-sentinel check on `SigningDeadline` causes premature outbound REVERT / duplicate-refund risk in SVM resolver - (File: `universalClient/tss/txresolver/svm.go`, `x/uexecutor/keeper/create_outbound.go`, `universalClient/tss/txflow/parse.go`)

### Summary
The reported bug class is: a critical numeric value (`newTimestamp`) that can legitimately be `0` on an internal failure path is used unchecked to derive a downstream index/decision (`periodIndex`), instead of being validated or treated as a recognized "unset" sentinel. Push Chain has the same pattern with `SigningDeadline`: it can be silently set to `0` on a chain-config lookup failure, and that `0` is later consumed by the SVM outbound resolver as a real, already-passed Unix deadline rather than as "no deadline configured," triggering an immediate REVERT decision on outbounds that may still be legitimately in flight.

### Finding Description
`attachOutboundsToUtx` in [1](#0-0)  computes `signingDeadline` for every newly created outbound:

```go
var signingDeadline int64
if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
    if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
        signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
    }
}
```

If `GetChainConfig` errors, or `TssSigningDeadline` is `nil`/`<= 0`, `signingDeadline` silently stays `0` — there is no validation, no fallback to a "no deadline" sentinel, and no rejection of the outbound-creation path. This zero value is persisted verbatim into `PendingOutboundEntry.SigningDeadline` and propagated into the `OutboundCreatedEvent`, confirmed by the existing test at [2](#0-1) , whose own comment states: `"signing_deadline should be 0 when chain config is not found"`.

That `0` later reaches the universalClient side through `ReadSigningDeadline`, whose own doc comment concedes the ambiguity: "Returns 0 if the event is unparseable **or the deadline was never set**" [3](#0-2) .

The SVM resolver then uses this value directly as a real deadline with no zero-guard:

```go
deadline := txflow.ReadSigningDeadline(event)
...
switch {
case clusterTime == 0:
    ...
case time.Now().Unix()-clusterTime > svmClusterStaleSeconds:
    ...
case clusterTime <= deadline+svmRevertSlackSeconds:
    // still inside window, retry
default:
}
_ = r.voteOutboundFailureAndMarkReverted(...)
``` [4](#0-3) 

When `deadline == 0`, the "inside deadline window" branch (`clusterTime <= deadline + 30`) is essentially always false for any real on-chain cluster timestamp, so the very first time the resolver checks a BROADCASTED-but-not-yet-landed outbound (PDA absent), it immediately falls into `voteOutboundFailureAndMarkReverted`, marking a real, still-pending tx as REVERTED. This is exactly the reported bug class: the invalid/zero value that "is explicitly stated to cause problems" elsewhere in the codebase (the `0`-means-"no expiry" convention is honored correctly for `ExpiryBlockHeight` in `GetNonExpiredConfirmedEvents` [5](#0-4) ) but is *not* honored for `SigningDeadline` in the SVM resolver path.

### Impact Explanation
Marking an outbound REVERTED while the corresponding transaction may still land on the destination chain breaks the "revert flow" and "refund accounting" invariants called out in the allowed-impact gate. A REVERT vote drives refund/compensation logic on Push Chain (revert outbound / fund-recipient refund), while the original signed TSS transaction can still independently confirm on the destination chain later. This can produce a duplicate settlement: the user is refunded on Push Chain via the false-REVERT path while the original outbound also executes on the destination chain, resulting in unauthorized double release of protocol/user funds — squarely in the "unauthorized refund" / "unauthorized release" impact category.

### Likelihood Explanation
This is reachable purely through ordinary chain configuration state (any destination chain that has no `TssSigningDeadline` set, or for which the config lookup transiently fails) combined with ordinary user cross-chain activity that creates an outbound — no malicious validator, TSS participant, or admin action is required to trigger the code path once such a chain exists. However, whether `TssSigningDeadline` is left unset in practice is a governance/registry configuration choice, which introduces some uncertainty about how frequently this zero-value state actually occurs in a properly operated deployment; I was not able to confirm from the available code whether `TssSigningDeadline` is mandatorily set for every registered chain at genesis/registration time.

### Recommendation
- Treat `SigningDeadline == 0` explicitly as "no deadline configured" throughout the resolver/broadcaster/txflow logic (mirroring the `ExpiryBlockHeight == 0` convention already used elsewhere), and skip the deadline-based REVERT branch in that case.
- Alternatively, fail closed in `attachOutboundsToUtx`: if `GetChainConfig` errors or `TssSigningDeadline` is unset, either reject outbound creation for that chain or apply a safe, non-zero default deadline, rather than silently persisting `0`.
- Add explicit unit/integration tests asserting that a `SigningDeadline` of `0` never causes an immediate REVERT decision in `resolveSVM`.

### Proof of Concept
1. Register (or leave unregistered/without `TssSigningDeadline`) a destination chain config in `x/uregistry`.
2. Submit an ordinary inbound (e.g., FUNDS_AND_PAYLOAD) that creates an outbound to that chain; `attachOutboundsToUtx` stores `PendingOutboundEntry.SigningDeadline = 0` (confirmed test: [2](#0-1) ).
3. TSS signs and the universalClient broadcasts the outbound to the SVM chain (`StatusBroadcasted`).
4. Before the transaction's PDA is observable on-chain, `resolveSVM` runs: `deadline := txflow.ReadSigningDeadline(event)` returns `0`; `clusterTime` (a real Unix timestamp) is `> 30`, so `clusterTime <= deadline+30` is false, and the resolver immediately calls `voteOutboundFailureAndMarkReverted`, marking a still-valid, still-broadcasting transaction as REVERTED.
5. If the transaction later lands on the destination chain (it was validly signed and broadcast), the user has effectively received both a refund/compensation via the false REVERT and the original outbound settlement.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L355-369)
```go
			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
```

**File:** x/uexecutor/keeper/pending_outbound_test.go (L326-332)
```go
	err := f.k.TestAttachOutboundsToUtx(f.ctx, "utx-dl-3", []*types.OutboundTx{outbound}, "")
	require.NoError(err)

	entry, err := f.k.PendingOutbounds.Get(f.ctx, "outbound-dl-3")
	require.NoError(err)
	require.Equal(int64(0), entry.SigningDeadline,
		"signing_deadline should be 0 when chain config is not found")
```

**File:** universalClient/tss/txflow/parse.go (L42-51)
```go
// ReadSigningDeadline extracts the chain-emitted signing deadline from a
// signed outbound event payload. Returns 0 if the event is unparseable or
// the deadline was never set (legacy events).
func ReadSigningDeadline(event *store.Event) int64 {
	var data SignedOutboundData
	if err := json.Unmarshal(event.EventData, &data); err != nil {
		return 0
	}
	return data.SigningDeadline
}
```

**File:** universalClient/tss/txresolver/svm.go (L60-92)
```go
	executed, clusterTime, err := builder.IsAlreadyExecuted(ctx, txID)
	if err != nil {
		log.Debug().Err(err).Msg("SVM PDA check failed, will retry next tick")
		return
	}

	if executed {
		if err := r.eventStore.Update(event.EventID, map[string]any{"status": store.StatusCompleted}); err != nil {
			log.Warn().Err(err).Msg("failed to mark SVM event COMPLETED")
			return
		}
		log.Info().Msg("event marked as COMPLETED")
		return
	}

	// PDA absent. Decide REVERT using the cluster's own clock so we don't
	// false-revert during halt/stall or host clock skew.
	deadline := txflow.ReadSigningDeadline(event)

	dlog := log.With().Int64("signing_deadline", deadline).Int64("cluster_block_time", clusterTime).Logger()
	switch {
	case clusterTime == 0:
		dlog.Debug().Msg("SVM cluster time unavailable, deferring REVERT decision")
		return
	case time.Now().Unix()-clusterTime > svmClusterStaleSeconds:
		dlog.Warn().Msg("SVM cluster appears stale, deferring REVERT")
		return
	case clusterTime <= deadline+svmRevertSlackSeconds:
		dlog.Debug().Msg("SVM PDA absent but cluster clock still inside deadline window, will retry next tick")
		return
	}

	_ = r.voteOutboundFailureAndMarkReverted(ctx, event, txID, utxID, "", 0, "0", "tx not executed on destination chain")
```

**File:** universalClient/tss/eventstore/store.go (L192-200)
```go
func (s *Store) GetNonExpiredConfirmedEvents(currentBlock, minBlockConfirmation uint64, limit int) ([]store.Event, error) {
	var minBlock uint64
	if currentBlock > minBlockConfirmation {
		minBlock = currentBlock - minBlockConfirmation
	}

	query := s.db.Where("status = ? AND block_height <= ? AND (expiry_block_height = 0 OR expiry_block_height > ?)",
		store.StatusConfirmed, minBlock, currentBlock).
		Order("block_height ASC, created_at ASC")
```
