### Title
Strict "chain height must strictly increase" gate on `VoteChainMeta` stalls the gas-price/chain-height oracle for L2 destination chains with static or slow-advancing block numbers - (File: x/uexecutor/keeper/chain_meta.go)

### Summary
The reported SphereX bug is a class of "false positive from non-monotonic/static block context" issue: hashing/gating logic keyed on `block.number` breaks on L2s (Arbitrum, Optimism, zkSync) where the number can stay constant across many real-time transactions, causing legitimate activity to be wrongly rejected. Push Chain's `VoteChainMeta` keeper method contains a structurally identical pattern: it hard-rejects any Universal Validator vote whose observed destination-chain height is not *strictly greater* than the last-applied height, with no time-based fallback path once bootstrapped.

### Finding Description
`Keeper.VoteChainMeta` [1](#0-0)  enforces:

```go
if bootstrapped && blockNumber <= entry.LastAppliedChainHeight {
    ...
    return fmt.Errorf("vote chain height %d is not greater than last applied chain height %d; re-vote with a newer block", ...)
}
```

Once the oracle for a given `observedChainId` has been bootstrapped (`LastAppliedChainHeight > 0`), any vote whose `ChainHeight` is `<=` the last applied height is rejected outright and **never stored** (the function returns before the vote is appended to `entry.Signers/Prices/ChainHeights/StoredAts`). The `blockNumber` field comes directly from each Universal Validator's own RPC read of `GetLatestBlock`/`GetLatestSlot` on the external chain [2](#0-1) , submitted via `MsgVoteChainMeta` [3](#0-2) .

On chains where `block.number` is derived from the L1 origin block (Arbitrum) or advances in bursts (Optimism/zkSync), the value observed by validators can remain unchanged for extended real-world periods, exactly as described in the external report. During that window:
- Any UV whose poll lands on the same still-current height as `LastAppliedChainHeight` is rejected, and their observation (including the up-to-date `price`) is discarded entirely rather than merged.
- Because the oracle updates `LastAppliedChainHeight` to the **median height across fresh votes** [4](#0-3) , once that median reaches the network's "stuck" height, essentially all subsequent votes for that height are rejected until the L2's number finally advances — even though gas price on the destination chain may have moved significantly in the meantime.
- There is no staleness-based override: the `chainMetaVoteStalenessSeconds` window only filters which *already-recorded* votes are eligible for the median [5](#0-4) ; it does not let a same-height-but-time-stale vote through the height gate.

### Impact Explanation
`ChainMeta` (gas price + chain height) drives `CallUniversalCoreSetChainMeta`, which is consumed downstream for gas price computation for crosschain payload execution/refunds [6](#0-5) . If the oracle for an L2 destination chain freezes on a stuck height, Push Chain's view of that chain's gas price also freezes at whatever value happened to be median at that height, becoming stale for the duration of the stall, which can misprice gas fee accounting/refunds for inbound/outbound flows tied to that chain. This is a liveness/staleness issue reachable purely from honest validators observing ordinary, unprivileged external-chain behavior — no malicious peer or admin action is required — matching the in-scope "denial of service... not network-level and reachable without privileged control" and "corruption of ... gas fee accounting ... chain config use" categories.

### Likelihood Explanation
This is not attacker-triggered but is deterministically reachable whenever Push Chain integrates a connected chain whose block-number semantics behave like Arbitrum/Optimism/zkSync (all three explicitly named in the source report), which is realistic given Push Chain's multi-chain hub-and-spoke design [7](#0-6) . Every polling cycle where the observed height doesn't strictly exceed the last applied height triggers the rejection path, so the likelihood of hitting this scales with how frequently the connected chain's block number stalls relative to the oracle's poll interval (default 30s) [8](#0-7) .

### Recommendation
Decouple "vote acceptance/aggregation" from "strict height monotonicity." Options: (a) allow votes with `blockNumber == LastAppliedChainHeight` to still update `price`/`storedAt` and participate in the price median (only reject strictly decreasing heights, or use `>=`), or (b) apply the existing time-based staleness window as the primary freshness gate and drop the strict height inequality requirement, so price observations continue to flow even while the observed chain's block number is temporarily static.

### Proof of Concept
Not directly exploitable by an external attacker; reachable via ordinary chain progression on integrated L2s. Reproduction requires simulating the destination-chain client returning a non-increasing `blockNumber` across consecutive `MsgVoteChainMeta` submissions once `LastAppliedChainHeight` has been set, and observing that `VoteChainMeta` returns the "not greater than last applied chain height" error and the vote is not merged into the median, which can be verified directly against the logic at [9](#0-8) .

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L16-19)
```go
const (
	// chainMetaVoteStalenessSeconds is the maximum age (in seconds) of a stored vote
	// that is still eligible to be included in the median calculation.
	chainMetaVoteStalenessSeconds uint64 = 300
```

**File:** x/uexecutor/keeper/chain_meta.go (L62-85)
```go
func (k Keeper) VoteChainMeta(ctx context.Context, universalValidator sdk.ValAddress, observedChainId string, price, blockNumber uint64) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	now := uint64(sdkCtx.BlockTime().Unix())

	entry, _, err := k.GetChainMeta(ctx, observedChainId)
	if err != nil {
		return sdkerrors.Wrap(err, "failed to fetch chain meta entry")
	}
	bootstrapped := entry.LastAppliedChainHeight > 0

	// Stale-height check applies only after bootstrap. During cold-start there
	// is no committed reference height yet, so any positive vote is acceptable.
	if bootstrapped && blockNumber <= entry.LastAppliedChainHeight {
		k.Logger().Warn("chain meta vote rejected: stale block height",
			"chain_id", observedChainId,
			"validator", universalValidator.String(),
			"vote_height", blockNumber,
			"last_applied_height", entry.LastAppliedChainHeight,
		)
		return fmt.Errorf(
			"vote chain height %d is not greater than last applied chain height %d; re-vote with a newer block",
			blockNumber, entry.LastAppliedChainHeight,
		)
	}
```

**File:** x/uexecutor/keeper/chain_meta.go (L156-177)
```go
	// Compute independent upper medians (len/2) for price and chain height.
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })

	k.Logger().Debug("chain meta medians computed",
		"chain_id", observedChainId,
		"fresh_votes", len(fresh),
		"median_price", medianPrice,
		"median_chain_height", medianChainHeight,
	)

	// Update MedianIndex to reflect the price median position in the full slice
	// (best-effort; used for storage/querying only).
	entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))

	priceBig := math.NewUint(medianPrice).BigInt()
	chainHeightBig := math.NewUint(medianChainHeight).BigInt()
	if _, evmErr := k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig); evmErr != nil {
		return sdkerrors.Wrap(evmErr, "failed to call EVM setChainMeta")
	}

	entry.LastAppliedChainHeight = medianChainHeight
```

**File:** universalClient/chains/evm/chain_meta_oracle.go (L58-69)
```go
// fetchAndVoteChainMeta periodically fetches gas price and votes on it
func (g *ChainMetaOracle) fetchAndVoteChainMeta(ctx context.Context) {
	defer g.wg.Done()

	// Get gas oracle fetch interval from config
	interval := g.getChainMetaOracleFetchInterval()
	if interval <= 0 {
		interval = 30 * time.Second
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()
```

**File:** universalClient/chains/evm/chain_meta_oracle.go (L96-119)
```go

			// Get current block number
			blockNumber, err := g.rpcClient.GetLatestBlock(ctx)
			if err != nil {
				g.logger.Error().Err(err).Msg("failed to get latest block number")
				continue
			}

			// Apply markup to gas price to handle spikes
			if g.gasPriceMarkupPercent > 0 {
				markup := new(big.Int).Mul(gasPrice, big.NewInt(int64(g.gasPriceMarkupPercent)))
				markup.Div(markup, big.NewInt(100))
				gasPrice.Add(gasPrice, markup)

				g.logger.Debug().
					Str("chain", g.chainID).
					Int("markup_percent", g.gasPriceMarkupPercent).
					Str("adjusted_gas_price", gasPrice.String()).
					Msg("applied gas price markup")
			}

			// Vote on chain meta (gas price + block height)
			priceUint64 := gasPrice.Uint64()
			voteTxHash, err := g.pushSigner.VoteChainMeta(ctx, g.chainID, priceUint64, blockNumber)
```

**File:** x/uexecutor/types/msg_vote_chain_meta.go (L14-25)
```go
func NewMsgVoteChainMeta(
	sender sdk.Address,
	observedChainId string,
	price, chainHeight uint64,
) *MsgVoteChainMeta {
	return &MsgVoteChainMeta{
		Signer:          sender.String(),
		ObservedChainId: observedChainId,
		Price:           price,
		ChainHeight:     chainHeight,
	}
}
```

**File:** app/README.md (L43-58)
```markdown
## What It Does

### The Hub-and-Spoke Picture

Push Chain is the coordination layer in a hub-and-spoke crosschain model. Universal Validators (the off-chain `puniversald` worker — see [`universalClient/README.md`](../universalClient/README.md)) watch external chains, observe events, run TSS, and vote those observations onto Push Chain. The core validator is the hub: it tallies those votes, executes the resulting Push Chain logic, and emits the next round of work.

```
    Ethereum ----\                            /---- Ethereum
    Arbitrum -----\    +------------------+  /---- Arbitrum
    Base ---------->---|   Push Chain     |--<---- Base
    BSC ----------/    | (core validator) |  \---- BSC
    Solana ------/     +------------------+   \--- Solana

         Inbound           Tally + Execute        Outbound
    (UV votes inbound)    (PC executes UTX)   (UV signs + relays)
```
```
