### Title
Unbounded, un-prioritized `PendingOutbounds` FIFO queue capped at a 1000-entry fetch enables outbound-processing starvation DoS - (File: `x/uexecutor/keeper/query_server.go`, `universalClient/pushcore/pushCore.go`, `universalClient/chains/push/event_listener.go`)

### Summary
Push Chain's outbound-processing pipeline mirrors the VUSD `withdrawals` queue pattern flagged in the report: an unbounded, append-only, oldest-first queue (`PendingOutbounds`) whose consumer (`puniversald`'s outbound poller) only ever fetches a fixed window (1000 entries) sorted ascending by creation height. There is no per-account/per-chain rate limit on how many outbounds a single unprivileged user can cause to be enqueued, and expired/stuck ballots are explicitly never cleaned out of the index. An attacker who can cheaply cause many inbound observations (e.g. many small/zero-amount transactions on a low-fee external chain) can flood this queue and starve legitimate users' outbounds from ever being fetched by the polling window, exactly the "queue-clogging" DoS class described in the VUSD report.

### Finding Description
`PendingOutbounds` entries are created directly at outbound creation time (before any vote), as documented in `x/uexecutor/README.md:262-270`, and are only removed when validators reach consensus on the outbound observation — ballot **expiry does not remove the entry**: [1](#0-0) 

The Universal Validator client fetches this queue with a hard-coded page limit of 1000, sorted oldest-first: [2](#0-1) 

That result feeds directly into the local event store that drives actual TSS signing/broadcast work: [3](#0-2) 

On the chain side, `AllPendingOutbounds` walks the *entire* `PendingOutbounds` collection into memory before applying pagination — the comment even states "pending set is small", i.e. there is no protocol-level cap or per-address throttle preventing that set from growing unbounded: [4](#0-3) 

Any ordinary, non-isCEA inbound that fails execution validation (e.g. a zero-amount `GAS`/`FUNDS` inbound, or any other `ValidateForExecution` failure) automatically spawns an `INBOUND_REVERT` outbound and its own `PendingOutbounds` entry: [5](#0-4) [6](#0-5) 

Since inbound creation only requires a real (but arbitrarily cheap) transaction to a gateway on any *enabled* external chain, an attacker can generate a large number of these entries at low marginal cost — no minimum-value floor comparable to VUSD's "5 VUSD" gate exists that would meaningfully throttle spam volume relative to attacker cost, and there is no per-sender cap on outstanding pending outbounds analogous to what the report recommends adding to VUSD.

### Impact Explanation
If an attacker enqueues more than 1000 `PendingOutbounds` entries whose corresponding outbound observations take a long time to reach 2/3 quorum (e.g., entries targeting congested/expensive external chains, or entries where the attacker doesn't cooperate with timely destination confirmation), those entries occupy the oldest positions in the ascending-sorted queue. `GetAllPendingOutbounds`'s hard 1000-item limit means honest users' newer outbounds are never returned to `puniversald`'s poller, so they never get TSS-signed or relayed — a legitimate-user fund-release/withdrawal DoS reachable purely through ordinary, unprivileged inbound submission, with honest validators and honest nodes behaving as designed. This matches "denial of service ... reachable without privileged control" in the allowed-impact scope.

### Likelihood Explanation
Medium. The attack is unprivileged and requires no compromise of validators/TSS — only the ability to submit ordinary (even failing/zero-amount) transactions on any enabled external chain, which is by design open to any user. The main friction is external gas cost per spam entry and confirmation latency, so the practical severity depends on the cheapest enabled chain's fee level; on any low-fee chain (or under `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` zero-amount execution, which is explicitly allowed with zero amount) this becomes economically trivial.

### Recommendation
- Add a per-sender (or per-source-chain) cap on the number of concurrently outstanding `PendingOutbounds`/pending-inbound-triggered-outbound entries, mirroring the VUSD recommendation of limiting withdrawal requests per address.
- Change the pending-outbound query to prioritize by something other than pure FIFO creation order (e.g., round-robin per source-chain/sender, or a minimum-value/priority ordering) so a single spammer's backlog cannot monopolize the fetch window.
- Reconsider the "ballot expiry does not remove the entry" design decision for `PendingOutbounds` (currently documented as intentional) so that stalled/abandoned outbound observations do not permanently occupy queue slots ahead of legitimate ones — or at minimum exclude long-idle/expired-ballot entries from the polling window so genuine outbounds are not starved behind them.

### Proof of Concept
1. Attacker submits N (>1000) cheap/zero-amount `GAS` or `FUNDS` inbound transactions on any enabled external chain such that each fails `ValidateForExecution` (or is a legitimate tiny transfer).
2. Each finalized inbound ballot creates a new `UniversalTx` with an `INBOUND_REVERT` (or normal) outbound, each inserted into `PendingOutbounds` (`x/uexecutor/keeper/pending_outbound.go`, created in `create_outbound.go`).
3. Once `len(PendingOutbounds) > 1000`, `Client.GetAllPendingOutbounds` (`universalClient/pushcore/pushCore.go:350-368`) — called by every `puniversald`'s `pollOutboundEvents` — always returns the oldest 1000 entries (per `AllPendingOutbounds`'s ascending `CreatedAt` sort in `x/uexecutor/keeper/query_server.go:417-424`).
4. A genuine user's outbound created after the attacker's backlog is never included in the returned page until enough of the attacker's older entries clear, delaying that user's fund release indefinitely relative to the attacker's queue depth.

### Citations

**File:** proto/uexecutor/v1/query.proto (L180-194)
```text
// Pending outbound index entry. Created by chain code at outbound creation
// (see create_outbound.go). Removed only when validators reach consensus
// on an OutboundObservation (see msg_vote_outbound.go). Ballot expiry does
// NOT remove the entry — operators investigate stuck outbounds via the
// per-variant audit trail (variants below) plus separate uvalidator ballot
// queries to see which ballots have terminated. See
// plan-pending-outbound-cleanup.md for design rationale.
message PendingOutboundEntry {
  string outbound_id     = 1;
  string universal_tx_id = 2;
  int64  created_at      = 3;
  int64  signing_deadline = 4; // unix timestamp after which the TSS signature expires on the destination chain (0 = no expiry)
  // Per-variant audit trail, populated as votes arrive (RecordOutboundVote).
  repeated OutboundObservationVariant variants = 5 [(gogoproto.nullable) = false];
}
```

**File:** universalClient/pushcore/pushCore.go (L350-368)
```go
// GetAllPendingOutbounds retrieves up to the first 1000 pending outbound transactions from Push Chain.
// Sorted by created_at (block height) ascending — oldest first.
func (c *Client) GetAllPendingOutbounds(ctx context.Context) ([]*uexecutortypes.PendingOutboundEntry, []*uexecutortypes.OutboundTx, error) {
	resp, err := retryWithRoundRobin(
		len(c.uexecutorClients),
		&c.rr,
		func(idx int) (*uexecutortypes.QueryAllPendingOutboundsResponse, error) {
			return c.uexecutorClients[idx].AllPendingOutbounds(ctx, &uexecutortypes.QueryAllPendingOutboundsRequest{
				Pagination: &query.PageRequest{Limit: 1000},
			})
		},
		"GetAllPendingOutbounds",
		c.logger,
	)
	if err != nil {
		return nil, nil, err
	}
	return resp.Entries, resp.Outbounds, nil
}
```

**File:** universalClient/chains/push/event_listener.go (L184-213)
```go
// pollOutboundEvents fetches pending outbounds and inserts them into the DB.
// Returns new event count.
func (el *EventListener) pollOutboundEvents(ctx context.Context) int {
	entries, outbounds, err := el.pushCore.GetAllPendingOutbounds(ctx)
	if err != nil {
		el.logger.Error().Err(err).Msg("failed to fetch pending outbounds")
		return 0
	}

	if len(entries) != len(outbounds) {
		el.logger.Error().
			Int("entries", len(entries)).
			Int("outbounds", len(outbounds)).
			Msg("mismatched entries and outbounds lengths")
		return 0
	}

	var newCount int
	for i, entry := range entries {
		event, err := convertOutboundToEvent(entry, outbounds[i])
		if err != nil {
			el.logger.Warn().Err(err).Str("outbound_id", entry.OutboundId).Msg("failed to convert outbound event")
			continue
		}

		newCount += el.storeEvent(event)
	}

	return newCount
}
```

**File:** x/uexecutor/keeper/query_server.go (L397-424)
```go
// AllPendingOutbounds implements types.QueryServer.
// Returns all pending outbound entries with full outbound data, sorted by block height.
// Uses pagination.reverse for descending order (default: ascending by created_at).
func (k Querier) AllPendingOutbounds(goCtx context.Context, req *types.QueryAllPendingOutboundsRequest) (*types.QueryAllPendingOutboundsResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "invalid request")
	}

	ctx := sdk.UnwrapSDKContext(goCtx)

	// Collect all entries (pending set is small)
	var allEntries []types.PendingOutboundEntry
	err := k.PendingOutbounds.Walk(ctx, nil, func(_ string, value types.PendingOutboundEntry) (bool, error) {
		allEntries = append(allEntries, value)
		return false, nil
	})
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}

	// Sort by CreatedAt (block height)
	reverse := req.Pagination.GetReverse()
	sort.Slice(allEntries, func(i, j int) bool {
		if reverse {
			return allEntries[i].CreatedAt > allEntries[j].CreatedAt
		}
		return allEntries[i].CreatedAt < allEntries[j].CreatedAt
	})
```

**File:** x/uexecutor/types/inbound.go (L126-138)
```go
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L409-439)
```go
	t.Run("GAS inbound with zero amount: vote succeeds, UTX has FAILED PCTx with revert outbound", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		inbound.TxHash = "0xgas0030"
		inbound.Amount = "0" // zero amount is not allowed for TxType_GAS

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "UTX should exist even when execution validation rejects the inbound")
		require.NotEmpty(t, utx.PcTx, "failed validation should be recorded as a PCTx")
		require.Equal(t, "FAILED", utx.PcTx[0].Status,
			"first PCTx should have FAILED status for zero-amount GAS inbound")
		require.Contains(t, utx.PcTx[0].ErrorMsg, "amount must be positive",
			"error message should indicate the amount constraint")

		// Zero-amount GAS inbound is treated like any other pre-execution failure:
		// a non-isCEA inbound creates an INBOUND_REVERT outbound.
		foundRevert := false
		for _, ob := range utx.OutboundTx {
			if ob.TxType == uexecutortypes.TxType_INBOUND_REVERT {
				foundRevert = true
				require.Equal(t, inbound.SourceChain, ob.DestinationChain)
				require.Equal(t, inbound.Amount, ob.Amount)
				break
			}
		}
		require.True(t, foundRevert, "INBOUND_REVERT should be created for zero-amount GAS inbound")
	})
```
