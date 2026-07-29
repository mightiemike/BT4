Confirmed: `GetLatestBlock` uses `client.BlockNumber(ctx)`, which returns the RPC's "latest" tip block, not a "finalized"/"safe" tagged block [1](#0-0) . This confirms the analog is present.

### Title
Inbound confirmation logic uses a fixed block-depth from `latest` instead of chain finality, allowing reorg-based unauthorized minting - (File: universalClient/chains/evm/event_confirmer.go)

### Summary
The Universal Validator's EVM event confirmer marks a gateway deposit event as `CONFIRMED` (and thus eligible to be voted into consensus via `VoteInbound`, ultimately minting PRC20 tokens on Push Chain) once a fixed number of blocks (`standardConfirmations`, default 12; `fastConfirmations`, default 5) have passed since the event's block, computed against `latest` chain head rather than a network-finalized reference point. This is the same bug class as the reported cdk-dinero-keeper issue: a fixed confirmation depth counted from the non-finalized tip provides no probabilistic guarantee against reorgs deeper than that depth, especially during network instability, short-reorg-prone L2s, or attacker-induced reorgs on the source chain.

### Finding Description
`EventConfirmer.processPendingEvents` fetches `latestBlock` via `ec.rpcClient.GetLatestBlock(ctx)` [2](#0-1) , which internally calls `client.BlockNumber(ctx)` — the chain tip, not a finalized/safe block [1](#0-0) . It then computes `confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1` and marks the event `CONFIRMED` once `confirmations >= requiredConfirmations` [3](#0-2) . `requiredConfirmations` is a small, fixed integer per confirmation type — default 5 for FAST, 12 for STANDARD (or the chain-config-supplied `BlockConfirmation.FastInbound`/`StandardInbound`) [4](#0-3) . The same pattern exists in the SVM confirmer using slots [5](#0-4) .

Once `CONFIRMED`, the event processor votes the observation into consensus via `MsgVoteInbound`/`VoteInbound`, and once 2/3+ of honest Universal Validators independently reach the same confirmation state and agree, `x/uexecutor` finalizes the ballot and executes the inbound — minting PRC20 tokens and running the user's payload [6](#0-5) . Nothing in this flow re-checks, after ballot finalization, whether the originally observed source-chain transaction is still canonical on the source chain. There is a `StatusReorged` status defined in the store model [7](#0-6)  suggesting reorg-handling was anticipated, but the confirmer path itself has no re-validation against a finalized tag (e.g., `eth_getBlockByNumber("finalized", ...)`) before promoting an event past `PENDING`.

If a reorg deeper than the configured confirmation depth occurs on the source chain after honest Universal Validators vote `CONFIRMED`/finalize the ballot but the underlying deposit transaction is later excluded from the canonical chain, Push Chain will have already minted PRC20 tokens (or executed the user's payload) for a deposit that no longer exists on the source chain. This is not attributable to any malicious validator or node — every Universal Validator behaves honestly and follows the exact same fixed-depth rule; the flaw is the choice of "latest minus N" instead of a network-native finality/safety concept.

### Impact Explanation
This falls squarely under the in-scope impact "unauthorized mint... of user or protocol-controlled funds" and "unauthorized state transitions in universal execution flows." A reorg that unwinds the deposit after inbound execution leaves PRC20 tokens minted on Push Chain with no backing deposit on the source chain — value created from nothing, or equivalently, an attacker who structures a deposit to be reorged out after collecting the honestly-minted Push Chain tokens effectively double-spends the source-chain funds. This requires no privileged actor: it can be caused by an ordinary attacker exploiting a source chain's reorg characteristics (e.g., an L2 sequencer reorg, a chain still recovering from a non-finality incident, or any environment where >12 block reorgs are feasible), consistent with the external report's characterization of the risk as "historically safe but no guarantee under edge/attack cases."

### Likelihood Explanation
Likelihood is low-to-medium under normal Ethereum L1 conditions (12-block reorgs are rare), but the confirmation counts and chain set are configurable per `ChainConfig`/`BlockConfirmation` for any CAIP-2 chain the registry admin adds, including L2s and other chains with weaker/variable reorg guarantees or during periods of degraded finality. Because the check is purely against `latest`, there is no mechanism that would automatically become safer during a chain-level non-finality incident — unlike a finalized-tag-based design, which would simply stall rather than confirm on top of unstable state.

### Recommendation
Follow the same remediation applied upstream in the referenced fix (PR 56, commit `3772bdd8`): where the destination chain type supports it (e.g., post-merge Ethereum and other chains exposing a finalized/safe head), confirm inbound events against the chain's `finalized` (or `safe`) block tag rather than (or in addition to) a fixed depth from `latest`. For chains without a native finality tag, keep the confirmation-count model but consider widening it and/or adding a periodic re-validation step (checking that a previously observed receipt/tx hash is still present at its original block) before/after ballot finalization, transitioning affected events to the existing `StatusReorged` state and reverting/refunding via the existing revert/refund flow if a mismatch is detected.

### Proof of Concept
1. Registry admin (or default config) sets `ChainConfig.BlockConfirmation.StandardInbound = 12` for a chain with reorg depth potentially exceeding 12 blocks (e.g., a fast/soft-finality L2 or a chain undergoing degraded finality).
2. Attacker sends a gateway deposit on the source chain.
3. `EventListener` records the event as `PENDING` at its observed block [8](#0-7) .
4. `EventConfirmer.processPendingEvents` observes `latestBlock - blockOfEvent + 1 >= 12` on all/most Universal Validators' independent RPC views and marks it `CONFIRMED` [3](#0-2) .
5. Validators vote `MsgVoteInbound`; ballot reaches 2/3+ and finalizes; `x/uexecutor` mints PRC20 tokens for the recipient [6](#0-5) .
6. A reorg deeper than 12 blocks occurs on the source chain (attacker-induced or incident-driven), removing the deposit transaction from the canonical chain.
7. Push Chain retains the minted PRC20 tokens with no corresponding locked/received value on the source chain — unauthorized mint / permanent loss of protocol-backed value.

### Citations

**File:** universalClient/chains/evm/rpc_client.go (L148-157)
```go
// GetLatestBlock returns the latest block number
func (rc *RPCClient) GetLatestBlock(ctx context.Context) (uint64, error) {
	var blockNum uint64
	err := rc.executeWithFailover(ctx, "get_block_number", func(client *ethclient.Client) error {
		var innerErr error
		blockNum, innerErr = client.BlockNumber(ctx)
		return innerErr
	})
	return blockNum, err
}
```

**File:** universalClient/chains/evm/event_confirmer.go (L101-107)
```go
// processPendingEvents fetches oldest 1000 pending events and checks if they are confirmed
func (ec *EventConfirmer) processPendingEvents(ctx context.Context) error {
	// Get latest block
	latestBlock, err := ec.rpcClient.GetLatestBlock(ctx)
	if err != nil {
		return fmt.Errorf("failed to get latest block: %w", err)
	}
```

**File:** universalClient/chains/evm/event_confirmer.go (L161-165)
```go
		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1

		if confirmations >= requiredConfirmations {
```

**File:** universalClient/chains/evm/event_confirmer.go (L248-267)
```go
// getRequiredConfirmations returns the required number of confirmations based on confirmation type
func (ec *EventConfirmer) getRequiredConfirmations(confirmationType string) uint64 {
	switch confirmationType {
	case store.ConfirmationFast:
		if ec.fastConfirmations >= 0 {
			return ec.fastConfirmations
		}
		return 5
	case store.ConfirmationStandard:
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	default:
		// Default to standard if unknown
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	}
```

**File:** universalClient/chains/svm/event_confirmer.go (L181-185)
```go
		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestSlot - txSlot + 1

		if confirmations >= requiredConfirmations {
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L86-99)
```go
	// --- Ballot finalized: always create UTX from here on ---
	k.Logger().Info("inbound ballot finalized, creating utx", "utx_key", universalTxKey, "source_chain", inbound.SourceChain)

	// Normalize inbound after finalization: strip irrelevant fields, decode raw_payload.
	// If normalization/decode fails, create UTX with failed PCTx + revert.
	if normalizeErr := inbound.NormalizeForTxType(); normalizeErr != nil {
		k.Logger().Warn("inbound normalization failed after ballot finalization",
			"utx_key", universalTxKey,
			"error", normalizeErr.Error(),
		)
		utx := types.UniversalTx{Id: universalTxKey, InboundTx: &inbound}
		if createErr := k.CreateUniversalTx(ctx, universalTxKey, utx); createErr != nil {
			return createErr
		}
```

**File:** universalClient/store/models.go (L10-20)
```go
// Event status values.
const (
	StatusPending     = "PENDING"     // Observed on external chain, awaiting confirmations
	StatusConfirmed   = "CONFIRMED"   // Confirmed (ready for processing or voting)
	StatusInProgress  = "IN_PROGRESS" // TSS signing in progress
	StatusSigned      = "SIGNED"      // TSS signing done, tx not yet broadcast
	StatusBroadcasted = "BROADCASTED" // Transaction sent to external chain
	StatusCompleted   = "COMPLETED"   // Successfully completed
	StatusReverted    = "REVERTED"    // Failed (expiry, receipt failed, or vote failed)
	StatusReorged     = "REORGED"     // Removed due to chain reorganization
)
```

**File:** universalClient/chains/evm/event_listener.go (L298-326)
```go
	for _, log := range logs {
		if len(log.Topics) == 0 {
			continue
		}

		// Determine event type based on topic
		eventType, ok := el.topicToEventType[log.Topics[0]]
		if !ok {
			continue
		}

		event := ParseEvent(&log, eventType, el.chainID, el.logger)
		if event != nil {
			// Insert event if it doesn't already exist
			if stored, err := el.chainStore.InsertEventIfNotExists(event); err != nil {
				el.logger.Error().Err(err).
					Str("event_id", event.EventID).
					Str("type", event.Type).
					Uint64("block", event.BlockHeight).
					Msg("failed to store event")
			} else if stored {
				el.logger.Debug().
					Str("event_id", event.EventID).
					Str("type", event.Type).
					Uint64("block", event.BlockHeight).
					Str("confirmation_type", event.ConfirmationType).
					Msg("stored new event")
			}
		}
```
