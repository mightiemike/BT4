## No vulnerability found for this question.

**Rationale:**

The claimed broken equality is "exactly one `MinerPaymentSchedule` row exists per `(consensus_hash)`". Tracing the code shows this framing is wrong: `insert_miner_payment_schedule` keys rows by `index_block_hash = StacksBlockId::new(consensus_hash, block_hash)` [1](#0-0) , and it is only invoked when `is_new_tenure` is true and a `scheduled_miner_reward` was actually computed via `calculate_scheduled_tenure_reward` [2](#0-1) . Two distinct blocks sharing a `consensus_hash` (i.e. two competing tenure-start candidates in a fork) will legitimately produce two distinct rows with different `block_hash`/`index_block_hash` — this is expected fork behavior, not a bug, and both rows being present in the `payments` table is harmless by itself.

The maturation path does **not** look up rows by `consensus_hash` alone. `get_matured_miner_reward_schedules` resolves the specific matured tenure header by walking the canonical ancestry from the current tip via `get_header_by_coinbase_height(tip_index_hash, matured_coinbase_height)`, and only then fetches `get_scheduled_block_rewards_at_block(&matured_tenure_block_header.index_block_hash())` [3](#0-2) . Because this walk follows `parent_block_id`/ancestry from the actual chain tip, it can only ever land on the block that is genuinely part of that tip's history. An orphaned sibling block sharing the same `consensus_hash` but sitting on a losing fork is never an ancestor of the canonical tip, so its `MinerPaymentSchedule` row is unreachable from this lookup — it cannot be "stale-row picked up" as the question hypothesizes.

Additionally, `find_mature_miner_rewards` pops exactly one 'miner' row from the list returned by that ancestry-based lookup (`latest_matured_miners.pop()`) [4](#0-3) , and `get_parent_matured_miner` similarly resolves the parent by `parent_consensus_hash`/`parent_block_hash` pointer chasing rather than a bare `consensus_hash` scan [5](#0-4) . `check_nakamoto_tenure` also independently enforces that a `BlockFound` tenure-change's `prev_tenure_consensus_hash` matches the parent block's own tenure, preventing malformed tenure linkage from being accepted at all [6](#0-5) .

Since maturation is fork-ancestry-scoped rather than keyed on a global `consensus_hash` index, the equality "at most one reachable/maturable miner-reward row per canonical tenure" holds both before and after the attacker's action. No double-payment path exists through this code; the scenario reduces to an ordinary, already-handled chain fork.

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L436-437)
```rust
        let index_block_hash =
            StacksBlockId::new(&block_reward.consensus_hash, &block_reward.block_hash);
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1000-1009)
```rust
        let latest_matured_miners_head = latest_matured_miners
            .first()
            .expect("latest_matured_miners should not be empty");
        assert!(latest_matured_miners_head.vtxindex == 0);
        assert!(latest_matured_miners_head.miner);

        let users = latest_matured_miners.split_off(1);
        let miner = latest_matured_miners
            .pop()
            .expect("BUG: no matured miners despite prior check");
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L5389-5402)
```rust
        let scheduled_miner_reward = if is_new_tenure {
            Some(Self::calculate_scheduled_tenure_reward(
                chainstate_tx,
                burn_dbconn,
                block,
                evaluated_epoch,
                parent_coinbase_height,
                chain_tip_burn_header_height.into(),
                burnchain_commit_burn,
                burnchain_sortition_burn,
            )?)
        } else {
            None
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L349-363)
```rust
        let matured_coinbase_height = coinbase_height - MINER_REWARD_MATURITY;
        let matured_tenure_block_header = Self::get_header_by_coinbase_height(
            chainstate_tx.deref_mut(),
            tip_index_hash,
            matured_coinbase_height,
        )?
        .ok_or_else(|| {
            warn!("Matured tenure data not found");
            ChainstateError::NoSuchBlockError
        })?;

        let latest_miners = StacksChainState::get_scheduled_block_rewards_at_block(
            chainstate_tx.deref_mut(),
            &matured_tenure_block_header.index_block_hash(),
        )?;
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L774-787)
```rust
        match tenure_payload.cause {
            TenureChangeCause::BlockFound => {
                // this tenure_payload's prev_consensus_hash must match the parent block tenure's
                // tenure_consensus_hash -- i.e. this tenure must be distinct from the parent
                // block's tenure
                if parent_tenure.tenure_id_consensus_hash
                    != tenure_payload.prev_tenure_consensus_hash
                {
                    warn!("Invalid tenure-change: tenure block-found does not confirm parent block's tenure";
                          "parent_tenure.tenure_consensus_hash" => %parent_tenure.tenure_id_consensus_hash,
                          "prev_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash
                    );
                    return Ok(None);
                }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L4699-4707)
```rust
        let parent_miner = if let Some(miner) = latest_matured_miners.first().as_ref() {
            StacksChainState::get_scheduled_block_rewards_at_block(
                conn,
                &StacksBlockHeader::make_index_block_hash(
                    &miner.parent_consensus_hash,
                    &miner.parent_block_hash,
                ),
            )?
            .pop()
```
