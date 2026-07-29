Based on the investigation, I found a concrete, unprivileged, gasless-reachable analog to the `minReward` DoS pattern.

### Title
Unbounded, fee-free growth of `AllPendingOutbounds`/`AllPendingInbounds` collections via zero-cost `MsgExecutePayload` outbound spam — full-scan query DoS - ([File: x/uexecutor/keeper/query_server.go], [File: x/uexecutor/keeper/create_outbound.go])

### Summary
Like the `RewardDistributor.distribute` bug (no `minReward` floor lets an attacker cheaply flood the `schedules` array, DoS-ing the unbounded loop in `getUnclaimedReward`), Push Chain's `MsgExecutePayload` is gasless (any signer, no Cosmos fee) and has no floor on how many distinct outbound-producing payload executions an attacker can submit. Each qualifying execution appends a new `PendingOutboundEntry` to `PendingOutbounds` (and transiently to `PendingInbounds` audit trails). The query handlers `AllPendingOutbounds` and `HasPendingOutboundsForChain` fully materialize/`Walk` the entire collection into memory before pagination/short-circuit, mirroring the original bug's unbounded loop.

### Finding Description
`MsgExecutePayload` is explicitly gasless for "any" signer per `x/uexecutor/README.md` (`app/txpolicy/gasless.go` whitelist), meaning submission costs zero Cosmos tx fee [1](#0-0) . Execution flows into `ExecutePayload`, which after a successful UEA call always calls `CreateUniversalTxFromReceiptIfOutbound`, and if the payload triggers a `UniversalTxOutbound` gateway event, a brand-new `UniversalTx` plus `PendingOutboundEntry` (keyed by a fresh deterministic `outbound_id`) is created and stored in `PendingOutbounds` [2](#0-1) [3](#0-2) .

An owner of a UEA who signs their own payload (needing no privileged role — signature check is enforced by the UEA contract against its own owner key, not by the chain module) can repeatedly submit `MsgExecutePayload` transactions, each incrementing the UEA's own nonce, each triggering a tiny outbound (e.g. a dust-value transfer). Since gas is deducted from the UEA's own PC balance rather than a Cosmos fee, and the underlying gateway call has no minimum-outbound-amount enforcement analogous to `minReward`, this is a cheap way to mass-produce `PendingOutboundEntry` records.

The query surface then fully scans this attacker-inflated collection before applying any limit:
- `AllPendingOutbounds` walks the *entire* `PendingOutbounds` collection into an in-memory slice, sorts it, and only then slices for pagination [4](#0-3) .
- `HasPendingOutboundsForChain` walks the entire collection on every call (documented as "O(n)") [5](#0-4) .

This is the direct analog of the reported bug: no floor value gates cheap creation of array/collection entries, and a downstream unbounded iteration (here, `Walk`/full materialization instead of a `for` loop over `schedules`) becomes increasingly expensive as the attacker-controlled collection grows.

### Impact Explanation
Impact is bounded to query-path resource exhaustion (gRPC/CLI query latency, node CPU/memory for `AllPendingOutbounds`, `HasPendingOutboundsForChain`), not a consensus state-transition failure — `PendingOutbounds` entries are never iterated during ordinary vote/finalization processing (`VoteOutbound` looks up entries by exact `outbound_id`, not by scanning). This limits severity relative to the original Solidity finding, which broke a consensus-critical (or at least reward-critical) unbounded loop. Node operators serving these queries (explorers, dashboards, admin tooling for chain-removal migrations that call `HasPendingOutboundsForChain`) would see degraded response times or increased resource usage as the attacker inflates the collection, but validators' block production and vote finalization paths are not blocked.

### Likelihood Explanation
Moderate-to-low. The attacker needs a funded UEA (their own) capable of paying the PC-side EVM gas for each `executeUniversalTx` + gateway-outbound call (the tx itself is fee-free, but EVM execution gas is still deducted from the UEA balance), and each qualifying outbound must pass registry checks (`IsChainOutboundEnabled`, valid `TokenConfig`) [6](#0-5) . There is no rate limit or minimum-value floor preventing an attacker from doing this thousands of times if they're willing to pay the EVM gas cost per call, which is exactly the same cost/effort tradeoff as the `minReward` bug (cheap-but-nonzero cost per spam entry, and no explicit floor to make it uneconomical for legitimate use vs. spam).

### Recommendation
- Add an admin-governable minimum outbound value / minimum interval-per-UEA-per-block for outbound creation triggered via `MsgExecutePayload`, analogous to enforcing `minReward` in the original report.
- Change `AllPendingOutbounds` and `HasPendingOutboundsForChain` to use collection-native paginated iteration (bounded `Walk` with an early-stop / `query.CollectionPaginate`, as already used correctly in `AllPendingInbounds`) instead of materializing the entire collection before pagination/short-circuiting.

### Proof of Concept
1. Attacker deploys/owns a UEA with a small PC balance.
2. Attacker repeatedly signs and submits `MsgExecutePayload` (fee-free) whose payload calls the gateway's outbound-triggering method with a dust amount, each with an incrementing UEA nonce.
3. Each call creates a new `UniversalTx` + `PendingOutboundEntry` via `CreateUniversalTxFromReceiptIfOutbound`/`attachOutboundsToUtx`.
4. After N such calls, `AllPendingOutbounds` and `HasPendingOutboundsForChain` must materialize/scan N entries per invocation, degrading query performance for legitimate callers (operators, migration tooling, explorers). [7](#0-6)

### Citations

**File:** app/txpolicy/gasless.go (L12-26)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L106-123)
```go
	// Step 5
	pcTx := types.PCTx{
		Sender:      evmFrom.Hex(),
		TxHash:      receipt.Hash,
		GasUsed:     receipt.GasUsed,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Step 6: create outbound + UTX only if needed
	if err := k.CreateUniversalTxFromReceiptIfOutbound(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
	if err := k.AttachRescueOutboundFromReceipt(sdkCtx, receipt, pcTx); err != nil {
		return err
	}

	return nil
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-67)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L160-185)
```go
func (k Keeper) CreateUniversalTxFromReceiptIfOutbound(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
	universalTxKey, err := k.BuildPcUniversalTxKey(ctx, pcTx)
	if err != nil {
		return errors.Wrap(err, "failed to create UniversalTx key")
	}

	outbounds, err := k.BuildOutboundsFromReceipt(ctx, universalTxKey, receipt)
	if err != nil {
		return err
	}

	if len(outbounds) == 0 {
		return nil
	}

	utx, err := k.CreateUniversalTxFromPCTx(ctx, pcTx)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
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

**File:** x/uexecutor/keeper/pending_outbound_query.go (L9-29)
```go
// HasPendingOutboundsForChain checks if there are any pending outbounds for a given chain.
// It walks PendingOutbounds and joins against UniversalTx to check destination_chain.
// Returns true on first match. This is O(n) but only called during admin-initiated migration.
func (k Keeper) HasPendingOutboundsForChain(ctx context.Context, chain string) (bool, error) {
	var found bool
	err := k.PendingOutbounds.Walk(ctx, nil, func(outboundId string, entry types.PendingOutboundEntry) (bool, error) {
		utx, exists, err := k.GetUniversalTx(ctx, entry.UniversalTxId)
		if err != nil {
			return true, err
		}
		if !exists {
			return false, nil
		}
		for _, ob := range utx.OutboundTx {
			if ob.DestinationChain == chain && ob.Id == outboundId {
				found = true
				return true, nil // stop walking
			}
		}
		return false, nil
	})
```
