### Title
`MinerReward::is_child()`/`is_parent()` misclassify a block's own reward when its coinbase legitimately equals 0, causing a deterministic panic in matured-reward insertion/lookup - ([File: stackslib/src/chainstate/stacks/db/accounts.rs])

### Summary
`MinerReward::is_child()` is defined as `coinbase > 0 && tx_fees_streamed_produced == 0`, and `is_parent()` is defined as `coinbase == 0`. When a real block's own coinbase (`total_coinbase = coinbase_at_block.saturating_add(accumulated_rewards)`) legitimately evaluates to 0 (e.g. at a coinbase phase-out/halving boundary), the block's own `MinerReward` record (which always has `tx_fees_streamed_produced == 0`) is wrongly classified as a "parent" record instead of a "child" record. This breaks the invariant relied on by `insert_matured_child_miner_reward` and `get_matured_miner_payment`, causing a deterministic `assert!`/`panic!` on every node that processes the tenure's maturation.

### Finding Description
The broken equality: for the `MinerReward` record representing tenure `T`'s own block reward (the "child" record, always constructed with `tx_fees_streamed_produced: 0`, see `calculate_miner_reward` at [1](#0-0) ), the system requires `record.is_child() == true` for *all* valid coinbase values, including `coinbase == 0`. This equality is broken because `is_child()`/`is_parent()` are defined purely in terms of `coinbase`: [2](#0-1) 

When `coinbase == 0` for the child record, `is_child()` returns `false` (fails the `coinbase > 0` check) while `is_parent()` returns `true` — mis-tagging the child as a parent.

Path to reach a zero coinbase for a real block: `calculate_scheduled_tenure_reward` computes `total_coinbase = coinbase_at_block.saturating_add(accumulated_rewards)` and passes it straight through to `make_scheduled_miner_reward` as the `coinbase` field with no floor/guard against 0: [3](#0-2) 

`coinbase_at_block` comes from `StacksChainState::get_coinbase_reward`, which delegates to the epoch's schedule (`epoch.coinbase_reward(...)`), whose value can legitimately reach 0 at phase-out/halving boundaries (the schedule is unbounded from below by design; no code path clamps it to a positive minimum). This is consistent with the question's stated precondition.

Once such a block matures, two consumers assume the coinbase-based classification is always correct:

1. `insert_matured_child_miner_reward` asserts `child_reward.is_child()` before insertion: [4](#0-3) 
If the block's own reward has `coinbase == 0`, this assertion fails and the node panics during block processing (called from `stackslib/src/chainstate/stacks/db/mod.rs` inside the block-advance path, i.e. inside consensus-critical block processing, see the call site at lines 2922-2941).

2. Even if insertion order or logic changed to avoid that specific panic, `get_matured_miner_payment` re-derives the same classification from the stored rows and panics identically: [5](#0-4) 
Because both stored rows (`try_add_parent` requires a distinguishable child) would now evaluate `is_parent() == true`, the branch hits `panic!("FATAL: got two parent rewards")` at line 678, or the reverse assertion `expect("FATAL: got two child rewards")` at lines 672/676.

Neither `check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, static validators, nor the MARF/state-root machinery touch this classification — the bug lives entirely in the post-hoc reward-bookkeeping logic that runs identically and deterministically on every full node once a tenure's rewards mature, so no existing guard intercepts it.

### Impact Explanation
This is a deterministic, network-wide panic: every honest node that advances its chain tip past the maturation height of a tenure whose own coinbase reward legitimately equals 0 will hit the same `assert!`/`panic!` inside block processing (`insert_matured_child_miner_reward` or `get_matured_miner_payment`). Because this code runs inside the mandatory block-advance path (used by every node to process every block, not just the miner), this results in a chain-wide processing halt ("freezing") once the network's block height reaches such a boundary — matching the Critical "permanent freezing" category. It requires no privileged role: any miner who legitimately wins a sortition during the affected tenure triggers the condition for the entire network, since all full nodes replay the same reward-maturation logic.

### Likelihood Explanation
The trigger condition depends entirely on the coinbase reward schedule (`get_coinbase_reward`/`epoch.coinbase_reward`) reaching exactly 0 for a valid, in-schedule block — which the prompt states is a legitimate, reachable state at halving/phase-out boundaries. No majority stake, no privileged role, and no unusual BTC spend are required: a normal single-miner-slot participant simply needs to win the sortition for the tenure at that height. This is fully repeatable and deterministic once the network height reaches such a boundary — it is a protocol-schedule-driven bug rather than an active-adversary exploit, but it satisfies the question's stated precondition and threat model (unprivileged, minority-stake attacker/participant).

### Recommendation
Do not classify `MinerReward` records solely by whether `coinbase > 0`. Instead, carry an explicit `is_parent`/role tag (or a separate `kind: {Child, Parent}` enum field) set at construction time in `calculate_miner_reward`/`make_scheduled_miner_reward`, so a legitimate zero-coinbase child reward is never conflated with a parent-fee-only reward. Update `insert_matured_child_miner_reward`, `insert_matured_parent_miner_reward`, and `get_matured_miner_payment` to key off this explicit tag rather than deriving role from `coinbase`/`tx_fees_streamed_produced` values that can coincidentally overlap.

### Proof of Concept
Rust integration test plan (place in `stackslib/src/chainstate/stacks/tests/accounting.rs` or `stackslib/src/chainstate/nakamoto/coordinator/tests.rs`):
1. Configure a `TestPeer`/burnchain such that `StacksChainState::get_coinbase_reward` returns exactly 0 for a target burn height (either by using an epoch/coinbase-schedule fixture engineered to phase out at a low, testable height, or by directly unit-testing `calculate_miner_reward` with a `MinerPaymentSchedule { coinbase: 0, .. }` input that has `miner: true` and legitimate non-punished status).
2. Mine tenures up through the point where the tenure's `total_coinbase` (per `calculate_scheduled_tenure_reward`) is 0 for a real, non-punished, non-poisoned block.
3. Assert, before the fix: that block processing panics inside `insert_matured_child_miner_reward` (`assert!(child_reward.is_child(), ...)`) or, if avoided, that `StacksChainState::get_matured_miner_payment(...)` panics with `"FATAL: got two parent rewards"`.
4. Assert, after the fix: `get_matured_miner_payment` returns `Some(reward)` where `reward.coinbase == 0` and `reward.total()` equals the correct sum of the block's own fee components plus 0 coinbase, attributed to the correct miner address — with no panic on any node replaying the same tenure.

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L162-168)
```rust
    pub fn is_child(&self) -> bool {
        self.coinbase > 0 && self.tx_fees_streamed_produced == 0
    }

    pub fn is_parent(&self) -> bool {
        self.coinbase == 0
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L586-613)
```rust
    /// Store a child block's matured miner reward.  This is the block's coinbase, anchored tx fees, and
    /// share of the confirmed streamed tx fees
    pub fn insert_matured_child_miner_reward(
        tx: &mut DBTx<'_>,
        parent_block_id: &StacksBlockId,
        child_block_id: &StacksBlockId,
        child_reward: &MinerReward,
    ) -> Result<(), Error> {
        test_debug!(
            "Insert matured child miner reward for {}-{}: {:?}",
            parent_block_id,
            child_block_id,
            child_reward
        );
        assert!(
            child_reward.is_child(),
            "FATAL: tried to insert a non-child reward as the child reward"
        );
        assert_eq!(
            child_reward.vtxindex, 0,
            "FATAL: tried to insert a user reward as a miner reward"
        );
        StacksChainState::inner_insert_matured_miner_reward(
            tx,
            parent_block_id,
            child_block_id,
            child_reward,
        )
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L654-691)
```rust
    pub fn get_matured_miner_payment(
        conn: &DBConn,
        parent_block_id: &TenureBlockId,
        child_block_id: &TenureBlockId,
    ) -> Result<Option<MinerReward>, Error> {
        let config = StacksChainState::load_db_config(conn)?;
        let ret = StacksChainState::inner_get_matured_miner_payments(
            conn,
            parent_block_id,
            child_block_id,
        )?;
        if ret.len() == 2 {
            // unwrap, because we do a len check above.
            let ret_0 = ret.get(0).unwrap();
            let ret_1 = ret.get(1).unwrap();
            let reward = if ret_0.is_child() {
                ret_0
                    .try_add_parent(ret_1)
                    .expect("FATAL: got two child rewards")
            } else if ret_1.is_child() {
                ret_1
                    .try_add_parent(ret_0)
                    .expect("FATAL: got two child rewards")
            } else {
                panic!("FATAL: got two parent rewards");
            };
            Ok(Some(reward))
        } else if child_block_id.0
            == StacksBlockHeader::make_index_block_hash(
                &FIRST_BURNCHAIN_CONSENSUS_HASH,
                &FIRST_STACKS_BLOCK_HASH,
            )
        {
            Ok(Some(MinerReward::genesis(config.mainnet)))
        } else {
            Ok(None)
        }
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L969-977)
```rust
        let miner_reward = MinerReward {
            address: child_address,
            recipient: child_recipient,
            coinbase: coinbase_reward,
            tx_fees_anchored,
            tx_fees_streamed_produced: 0,
            tx_fees_streamed_confirmed,
            vtxindex: participant.vtxindex,
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L965-1030)
```rust
        let coinbase_at_block = StacksChainState::get_coinbase_reward(
            evaluated_epoch,
            chainstate_tx.config.mainnet,
            chain_tip_burn_header_height,
            burn_dbconn.context.first_block_height,
        );

        let total_coinbase = coinbase_at_block.saturating_add(accumulated_rewards);
        let parent_tenure_start_header: StacksHeaderInfo = Self::get_header_by_coinbase_height(
            chainstate_tx.deref_mut(),
            &block.header.parent_block_id,
            parent_coinbase_height,
        )?
        .ok_or_else(|| {
            warn!("While processing tenure change, failed to look up parent tenure";
                  "parent_coinbase_height" => parent_coinbase_height,
                  "parent_block_id" => %block.header.parent_block_id,
                  "consensus_hash" => %block.header.consensus_hash,
                  "stacks_block_hash" => %block.header.block_hash(),
                  "stacks_block_id" => %block.header.block_id()
            );
            ChainstateError::NoSuchBlockError
        })?;
        // fetch the parent tenure fees by reading the total tx fees from this block's
        // *parent* (not parent_tenure_start_header), because `parent_block_id` is the last
        // block of that tenure, so contains a total fee accumulation for the whole tenure
        let parent_tenure_fees = if parent_tenure_start_header.is_nakamoto_block() {
            Self::get_total_tenure_tx_fees_at(
                chainstate_tx,
                &block.header.parent_block_id
            )?.ok_or_else(|| {
                warn!("While processing tenure change, failed to look up parent block's total tx fees";
                      "parent_block_id" => %block.header.parent_block_id,
                      "consensus_hash" => %block.header.consensus_hash,
                      "stacks_block_hash" => %block.header.block_hash(),
                      "stacks_block_id" => %block.header.block_id()
                    );
                ChainstateError::NoSuchBlockError
            })?
        } else {
            // if the parent tenure is an epoch-2 block, don't pay
            // any fees to them in this schedule: nakamoto blocks
            // cannot confirm microblock transactions, and
            // anchored transactions are scheduled
            // by the parent in epoch-2.
            0
        };

        Ok(Self::make_scheduled_miner_reward(
            mainnet,
            evaluated_epoch,
            &parent_tenure_start_header.anchored_header.block_hash(),
            &parent_tenure_start_header.consensus_hash,
            &block.header.block_hash(),
            &block.header.consensus_hash,
            block.header.chain_length,
            block
                .get_coinbase_tx()
                .ok_or(ChainstateError::InvalidStacksBlock(
                    "No coinbase transaction in tenure changing block".into(),
                ))?,
            parent_tenure_fees,
            burnchain_commit_burn,
            burnchain_sortition_burn,
            total_coinbase,
        ))
```
