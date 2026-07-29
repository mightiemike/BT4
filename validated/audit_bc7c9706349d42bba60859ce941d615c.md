### Title
Zero-valued `SigningDeadline` for "no expiry" chains is indistinguishable from an already-expired deadline, causing outbound TSS transactions to never broadcast and be prematurely reverted - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`attachOutboundsToUtx` computes `signingDeadline` as an `int64` Unix timestamp for the outbound's TSS signature expiry. Per the proto docs, `TssSigningDeadline == nil` is an explicit, intentional "no expiry" configuration [1](#0-0) . The keeper collapses both this legitimate "never expires" case and the (error) "chain config not found" case to the same sentinel value, `0` [2](#0-1) . Downstream Universal Validator logic in `universalClient/tss/txbroadcaster/svm.go` and `universalClient/tss/txresolver/svm.go` treats `SigningDeadline` as an authoritative already-computed absolute timestamp with no special-case for `0`, so `0` is interpreted as "deadline already passed at the Unix epoch" rather than "no deadline." This mirrors the `getMultiplier` bug class exactly: a function silently returns a "valid-looking" value (`0`) that is ambiguous between a legitimate business state and an unhandled/edge condition, and calling code implicitly assumes the returned value is always meaningful.

### Finding Description
1. `attachOutboundsToUtx` only sets a non-zero `signingDeadline` when `TssSigningDeadline != nil && *TssSigningDeadline > 0`; otherwise `signingDeadline` stays at its zero value `0` [3](#0-2) . This `0` is written into `PendingOutboundEntry.SigningDeadline` and propagated into the `OutboundCreated` event and eventually into the signed outbound event data consumed by the Universal Validator node (`ReadSigningDeadline` / `data.SigningDeadline`) [4](#0-3) .
2. In `broadcastOutboundSVM`, the deadline is read directly as `deadline := data.SigningDeadline`, and the code branches on `now > deadline` [5](#0-4) . When `deadline == 0`, `now > 0` is always true (since `now` is a real, large Unix timestamp), so the code immediately enters the "past-deadline" branch on the very first attempt. Inside that branch, if the tx isn't already executed and `clusterTime > deadline` (0), which is essentially always true, the code marks the event `BROADCASTED("")` — i.e., **cluster-confirmed expired — without ever calling `BroadcastOutboundSigningRequest`** [6](#0-5) .
3. In `resolveSVM`, the same zero deadline causes `clusterTime <= deadline+svmRevertSlackSeconds` (i.e., `clusterTime <= 30`) to be false almost immediately (real cluster time is astronomically larger than 30), so the resolver falls straight through to `voteOutboundFailureAndMarkReverted`, marking the outbound as REVERTED [7](#0-6) .
4. Net effect: for any destination chain whose `ChainConfig.TssSigningDeadline` is left unset (which the proto explicitly documents as the valid, presumably common, "no expiry" configuration) [1](#0-0) , every outbound TSS transaction is treated as already expired the moment it is signed. It is never actually broadcast to the destination chain, yet the protocol proceeds through the revert/refund pathway as though the outbound attempt genuinely timed out.

### Impact Explanation
This breaks the universal-execution outbound/revert invariant for the affected chain(s): funds that should be delivered to the destination chain via the TSS-signed outbound are never sent, while the protocol's revert/refund logic runs as if a genuine timeout occurred (`voteOutboundFailureAndMarkReverted`) [7](#0-6) . This can result in permanent loss of the outbound funds (never delivered and, depending on revert semantics, potentially double-accounted if a peer manages to land the signed tx despite the broadcaster's premature skip — a race the broadcaster itself flags as `executed` after the fact) or a systematic failure that silently defeats a whole chain's outbound pipeline. This is triggered purely by ordinary user/UV activity against a chain configuration that is documented as valid and unprivileged (no admin misbehavior or malicious validator required) — any user routing an outbound through a chain with unset `tss_signing_deadline` hits this path.

### Likelihood Explanation
Likelihood is high in any deployment where a registered chain's `ChainConfig.TssSigningDeadline` is left unset — which the protobuf comment documents as the intended way to express "no expiry" [1](#0-0) , and which is exercised by dedicated unit tests confirming `SigningDeadline` is `0` in that case [8](#0-7) . No attacker action beyond normal use of the crosschain outbound flow is required; the bug fires automatically the first time the broadcaster/resolver polling loop runs against such an outbound.

### Recommendation
Do not conflate "no expiry configured" with "already expired." Use an explicit sentinel that downstream code checks before comparing timestamps — e.g., keep `SigningDeadline == 0` meaning "no deadline" and add explicit `if deadline == 0 { /* skip deadline checks entirely */ }` guards in both `broadcastOutboundSVM` and `resolveSVM` before doing the `now > deadline` / `clusterTime <= deadline+slack` comparisons, mirroring the recommendation for `getMultiplier`: make the zero/"no value" case an explicit, checked branch rather than an implicit numeric comparison that happens to work out only for legitimate non-zero deadlines.

### Proof of Concept
1. Register (or use an existing) SVM `ChainConfig` with `TssSigningDeadline` left unset/nil (a valid, documented configuration).
2. Any user submits a crosschain transaction whose payload/outbound resolves to that chain; `attachOutboundsToUtx` creates a `PendingOutboundEntry` with `SigningDeadline = 0` [9](#0-8) .
3. Once the outbound is TSS-signed, `broadcastOutboundSVM` runs: `now > deadline` (0) is always true, so on the very first tick it checks `IsAlreadyExecuted`; since the tx has not been broadcast yet, it is not executed, and `clusterTime > deadline` (0) holds, so it marks the event `BROADCASTED("")` without ever calling `BroadcastOutboundSigningRequest` [10](#0-9) .
4. Shortly after, `resolveSVM` checks the PDA (absent, since it was never broadcast), computes `deadline := ReadSigningDeadline(event)` = 0, and since real `clusterTime` is far greater than `30`, falls through to `voteOutboundFailureAndMarkReverted`, marking the transaction REVERTED [7](#0-6) .
5. Result: the destination-chain transfer never happens, yet the system proceeds down the revert/refund path as if it had genuinely timed out — for every outbound on that chain, not just an isolated failure.

### Citations

**File:** proto/uregistry/v1/types.proto (L118-118)
```text
  google.protobuf.Duration tss_signing_deadline = 10 [(gogoproto.stdduration) = true]; // duration added to block time to compute the signature expiry deadline on the destination chain (zero = no expiry)
```

**File:** x/uexecutor/keeper/create_outbound.go (L355-368)
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

**File:** universalClient/tss/txbroadcaster/svm.go (L55-76)
```go
	deadline := data.SigningDeadline
	now := time.Now().Unix()

	// Past local deadline — confirm with the cluster before giving up.
	if now > deadline {
		executed, clusterTime, checkErr := builder.IsAlreadyExecuted(ctx, txID)
		dlog := log.With().Int64("signing_deadline", deadline).Int64("cluster_block_time", clusterTime).Logger()

		switch {
		case checkErr != nil:
			dlog.Debug().Err(checkErr).Msg("SVM cluster check failed at deadline, retry next tick")
			return
		case executed:
			dlog.Debug().Msg("SVM tx executed by peer past local deadline, marking BROADCASTED")
			b.markBroadcasted(event, chainID, "")
			return
		case clusterTime > deadline:
			dlog.Debug().Msg("SVM deadline cluster-confirmed expired, marking BROADCASTED for resolver REVERT")
			b.markBroadcasted(event, chainID, "")
			return
		}
		// Cluster says still inside the window (or freshness unknown) — broadcast.
```

**File:** universalClient/tss/txresolver/svm.go (L77-92)
```go
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

**File:** x/uexecutor/keeper/pending_outbound_test.go (L276-305)
```go
func TestPendingOutbound_SigningDeadline_NilDuration(t *testing.T) {
	f := setupPendingOutboundFixture(t)
	require := require.New(t)

	f.mockUregistryKeeper.EXPECT().
		GetChainConfig(gomock.Any(), "eip155:1").
		Return(uregistrytypes.ChainConfig{
			Chain:              "eip155:1",
			TssSigningDeadline: nil,
		}, nil).AnyTimes()

	utx := types.UniversalTx{Id: "utx-dl-2"}
	require.NoError(f.k.UniversalTx.Set(f.ctx, "utx-dl-2", utx))

	outbound := &types.OutboundTx{
		Id:               "outbound-dl-2",
		DestinationChain: "eip155:1",
		Recipient:        "0xRecipient",
		Amount:           "1000",
		OutboundStatus:   types.Status_PENDING,
	}

	err := f.k.TestAttachOutboundsToUtx(f.ctx, "utx-dl-2", []*types.OutboundTx{outbound}, "")
	require.NoError(err)

	entry, err := f.k.PendingOutbounds.Get(f.ctx, "outbound-dl-2")
	require.NoError(err)
	require.Equal(int64(0), entry.SigningDeadline,
		"signing_deadline should be 0 when chain has no tss_signing_deadline")
}
```
