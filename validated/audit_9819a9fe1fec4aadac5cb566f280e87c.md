Based on my investigation, I found a directly analogous pattern in this repo's Nakamoto block-gossip peer-scoring path.

### Title
Invalid pushed Nakamoto blocks bypass peer banning due to overly-narrow error match - (File: stackslib/src/net/relay.rs)

### Summary
`Relayer::process_pushed_nakamoto_blocks` only bans a peer when `process_new_nakamoto_block` returns the exact `chainstate_error::InvalidStacksBlock(msg)` variant. Many other error paths in block validation/acceptance (e.g. `accept_block`'s well-formedness checks, `PoxNoRewardCycle`, `DBError(NotFoundError)`, `Expects`, etc.) represent a peer having sent bad/invalid data, but are silently swallowed into a generic `Err(e) => warn!(...)` branch that does not add the offending peer to `bad_neighbors`.

### Finding Description
In `stackslib/src/net/relay.rs`, `process_pushed_nakamoto_blocks` iterates pushed blocks and matches on the result of `Self::process_new_nakamoto_block(...)`: [1](#0-0) 

Only the `Err(chainstate_error::InvalidStacksBlock(msg))` arm pushes the sender into `bad_neighbors`, which is later used to ban peers via `p2p.ban_peers(bad_neighbors)`: [2](#0-1) [3](#0-2) 

But `process_new_nakamoto_block` (and the `NakamotoChainState::accept_block` it calls) can return many other error variants for what is unambiguously an invalid or malicious block, e.g.:
- `NakamotoChainState::accept_block` returns generic `ChainstateError` from `is_wellformed_tenure_start_block()`, `is_wellformed_tenure_extend_block()`, `validate_normal_nakamoto_block_burnchain()` — these are not guaranteed to be the `InvalidStacksBlock` variant. [4](#0-3) 
- `Error` (aka `chainstate_error`) has many variants beyond `InvalidStacksBlock` that a peer's bad data can trigger, such as `DBError`, `NoSuchBlockError`, `Expects`, `PoxNoRewardCycle`. [5](#0-4) 

When any of these non-`InvalidStacksBlock` variants is returned for a genuinely bad block, the code falls into the catch-all `Err(e) => warn!(...)` branch, which never appends the peer to `bad_neighbors`, so the peer is never banned: [6](#0-5) 

This is structurally the same bug class as the Zebra advisory: the ban-decision code performs an overly narrow/incorrect check on the concrete error type/variant returned by the verifier, rather than checking "was this block invalid," so score/ban logic silently no-ops on legitimate misbehavior. Here the equality being broken is: "a peer that supplies a consensus-invalid gossiped Nakamoto block should always be added to `bad_neighbors` and banned" — but the pattern match only catches one out of many invalid-block error shapes.

### Impact Explanation
This is a minority-triggerable (single dishonest/malicious peer, no majority needed) failure of the peer-misbehavior mechanism. A malicious or misbehaving peer that supplies invalid Nakamoto blocks whose validation failure happens to surface as any `ChainstateError` variant other than `InvalidStacksBlock` will never be banned, and can repeat the push indefinitely, consuming network bandwidth, block deserialization, sortition/reward-set lookups, and burnchain-commit checks on the node without consequence. This matches the "High" bucket for temporary tip disagreement / minority-triggerable protection-mechanism bypass tier described in the rules, since no chain split or state-root divergence results — only the ban/misbehavior scoring guarantee is broken, mirroring the Zebra advisory's impact class exactly (protection-mechanism failure, not consensus failure).

### Likelihood Explanation
High likelihood of being reachable: any attacker-controlled peer can push a hand-crafted `NakamotoBlocksData` message with a malformed block designed to fail one of the non-`InvalidStacksBlock` checks in `accept_block` (e.g. a missing sortition snapshot causing `NoSuchBlockError`/`DBError`, or a malformed tenure-start/tenure-extend block causing an error variant other than `InvalidStacksBlock` depending on the exact validator invoked). This requires no special privileges, no majority, and no node-operator cooperation — a single unprivileged P2P peer suffices.

### Recommendation
Change `process_pushed_nakamoto_blocks` to treat any `Err(_)` from `process_new_nakamoto_block` that represents a bad/invalid block (as opposed to purely internal/transient errors like DB I/O failures unrelated to the block's content) as cause to add the neighbor to `bad_neighbors`. Concretely, either:
1. Broaden the match to cover all `ChainstateError` variants that indicate the block itself is invalid (not just `InvalidStacksBlock`), or
2. Introduce an explicit `is_block_invalid()` classification method on `ChainstateError` (similar to how `RouterError::misbehavior_score()` is meant to work in the Zebra fix) that the relay code calls to decide whether to ban, rather than matching on one specific enum variant.

### Proof of Concept
1. A malicious peer establishes a P2P connection to a victim node.
2. The peer pushes a well-formed-looking `NakamotoBlocksData` message containing a block that fails a check inside `NakamotoChainState::accept_block` other than the exact `InvalidStacksBlock` variant path — for example, a block whose consensus hash doesn't correspond to a known sortition snapshot (triggering `chainstate_error::DBError(db_error::NotFoundError)` at [7](#0-6) ), or a malformed tenure-start/tenure-extend block that fails `is_wellformed_tenure_start_block`/`is_wellformed_tenure_extend_block` with a non-`InvalidStacksBlock` error variant.
3. `process_new_nakamoto_block` returns `Err(e)` where `e` is not `chainstate_error::InvalidStacksBlock`.
4. In `process_pushed_nakamoto_blocks`, execution falls into the generic `Err(e) => warn!(...)` arm at [8](#0-7) , and the peer is never added to `bad_neighbors`.
5. `process_new_epoch3_blocks` calls `p2p.ban_peers(bad_neighbors)` only for peers that were collected, so the offending peer is never banned and can repeat step 2 indefinitely.

### Citations

**File:** stackslib/src/net/relay.rs (L961-969)
```rust
        let block_sn =
            SortitionDB::get_block_snapshot_consensus(sort_handle, &block.header.consensus_hash)?
                .ok_or_else(|| {
                debug!(
                    "Failed to load snapshot for consensus hash {}",
                    &block.header.consensus_hash
                );
                chainstate_error::DBError(db_error::NotFoundError)
            })?;
```

**File:** stackslib/src/net/relay.rs (L1696-1741)
```rust
                    match Self::process_new_nakamoto_block(
                        burnchain,
                        sortdb,
                        &mut sort_handle,
                        chainstate,
                        &network_result.stacks_tip,
                        &nakamoto_block,
                        coord_comms,
                        NakamotoBlockObtainMethod::Pushed,
                    ) {
                        Ok(accept_response) => match accept_response {
                            BlockAcceptResponse::Accepted => {
                                debug!(
                                    "Accepted Nakamoto block {} ({}) from {}",
                                    &block_id, &nakamoto_block.header.consensus_hash, neighbor_key
                                );
                                accepted_blocks.push(nakamoto_block);
                            }
                            BlockAcceptResponse::AlreadyStored => {
                                debug!(
                                    "Rejected Nakamoto block {} ({}) from {}: already stored",
                                    &block_id, &nakamoto_block.header.consensus_hash, &neighbor_key,
                                );
                            }
                            BlockAcceptResponse::Rejected(msg) => {
                                warn!(
                                    "Rejected Nakamoto block {} ({}) from {}: {:?}",
                                    &block_id,
                                    &nakamoto_block.header.consensus_hash,
                                    &neighbor_key,
                                    &msg
                                );
                            }
                        },
                        Err(chainstate_error::InvalidStacksBlock(msg)) => {
                            warn!("Invalid pushed Nakamoto block {}: {}", &block_id, msg);
                            bad_neighbors.push((*neighbor_key).clone());
                            break;
                        }
                        Err(e) => {
                            warn!(
                                "Could not process pushed Nakamoto block {}: {:?}",
                                &block_id, &e
                            );
                        }
                    }
```

**File:** stackslib/src/net/relay.rs (L2047-2060)
```rust
        let (pushed_blocks_and_relayers, bad_neighbors) = match Self::process_pushed_nakamoto_blocks(
            network_result,
            burnchain,
            sortdb,
            chainstate,
            coord_comms,
            connection_opts.reject_blocks_pushed,
        ) {
            Ok(x) => x,
            Err(e) => {
                warn!("Failed to process pushed Nakamoto blocks: {:?}", &e);
                (vec![], vec![])
            }
        };
```

**File:** stackslib/src/net/relay.rs (L2779-2785)
```rust
        // punish bad peers
        if !bad_neighbors.is_empty() {
            debug!("{:?}: Ban {} peers", &local_peer, bad_neighbors.len());
            if let Err(e) = self.p2p.ban_peers(bad_neighbors) {
                warn!("Failed to ban bad-block peers: {:?}", &e);
            }
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2846-2896)
```rust
        // if this is the first tenure block, then make sure it's well-formed
        block.is_wellformed_tenure_start_block().inspect_err(|_| {
            warn!("Block {block_id} is not a well-formed first tenure block");
        })?;

        // if this is a tenure-extend block, then make sure it's well-formed
        block.is_wellformed_tenure_extend_block().inspect_err(|_| {
            warn!("Block {block_id} is not a well-formed tenure-extend block");
        })?;

        // it's okay if this fails because we might not have the parent block yet.  It will be
        // checked on `::append_block()`
        let expected_burn_opt = Self::get_expected_burns(db_handle, headers_conn, block)?;

        if block.is_shadow_block() {
            // this block is already present in the staging DB, so just perform some prefunctory
            // validation (since they're constructed a priori to be valid)
            Self::validate_shadow_nakamoto_block_burnchain(
                staging_db_tx.conn(),
                db_handle,
                expected_burn_opt,
                block,
                config.mainnet,
                config.chain_id,
            )
            .unwrap_or_else(|e| {
                error!("Unacceptable shadow Nakamoto block";
                    "stacks_block_id" => %block_id,
                    "error" => ?e
                );
                panic!("Unacceptable shadow Nakamoto block");
            });
            return Ok(false);
        }

        // this block must be consistent with its miner's leader-key and block-commit, and must
        // contain only transactions that are valid in this epoch.
        Self::validate_normal_nakamoto_block_burnchain(
            staging_db_tx.conn(),
            db_handle,
            expected_burn_opt,
            block,
            config.mainnet,
            config.chain_id,
        )
        .inspect_err(|e| {
            warn!("Unacceptable Nakamoto block; will not store";
                "stacks_block_id" => %block_id,
                "error" => ?e
            );
        })?;
```

**File:** stackslib/src/chainstate/stacks/mod.rs (L83-140)
```rust
#[derive(Debug)]
pub enum Error {
    InvalidFee,
    InvalidStacksBlock(String),
    ExpectedTenureChange,
    InvalidStacksMicroblock(String, BlockHeaderHash),
    // The bool is true if the invalid transaction was quietly ignored.
    InvalidStacksTransaction(String, bool),
    /// This error indicates that the considered transaction was skipped
    /// because of the current state of the block assembly algorithm,
    /// but the transaction otherwise may be valid (e.g., block assembly is
    /// only considering STX transfers and this tx isn't a transfer).
    StacksTransactionSkipped(String),
    PostConditionFailed(String),
    NoSuchBlockError,
    /// The supplied Sortition IDs, consensus hashes, or stacks blocks are not in the same fork.
    NotInSameFork,
    InvalidChainstateDB,
    BlockTooBigError,
    BlockCostLimitError,
    TransactionTooBigError(Option<ExecutionCost>),
    BlockCostExceeded,
    NoTransactionsToMine,
    MicroblockStreamTooLongError,
    IncompatibleSpendingConditionError,
    CostOverflowError(ExecutionCost, ExecutionCost, ExecutionCost),
    /// Errors that occur during clarity contract processing and execution
    ClarityError(ClarityError),
    DBError(db_error),
    NetError(net_error),
    CodecError(codec_error),
    MARFError(marf_error),
    ReadError(io::Error),
    WriteError(io::Error),
    MemPoolError(String),
    PoxAlreadyLocked,
    PoxInsufficientBalance,
    PoxNoRewardCycle,
    PoxExtendNotLocked,
    PoxIncreaseOnV1,
    PoxInvalidIncrease,
    DefunctPoxContract,
    ProblematicTransaction(Txid),
    MinerAborted,
    ChannelClosed(String),
    /// This error indicates a Epoch2 block attempted to build off of a Nakamoto block.
    InvalidChildOfNakomotoBlock,
    NoRegisteredSigners(u64),
    TenureTooBigError,
    TxWouldNotFitError,
    /// This error indicates an internal state or condition that should never actually happen
    Expects(String),
    /// This error indicates that a transaction execution was aborted because it exceeded the maximum allowed execution time or memory use.
    ExecutionResourceBudgetExceeded(String),
    /// This error indicates that contract analysis was aborted because it exceeded the maximum allowed analysis time or memory use.
    /// Distinct from `ExecutionResourceBudgetExceeded` so an analysis-phase issue is separable in logs/metrics and in `is_problematic`.
    AnalysisResourceBudgetExceeded(String),
}
```
