Based on the code I retrieved, the claimed vulnerability does not hold up.

The equality the question claims is broken is: `get_header_by_coinbase_height(tip_index_hash, matured_coinbase_height)` must resolve to the unique ancestor of `tip_index_hash` at that coinbase height, and not a stale/wrong-fork block.

Tracing `get_header_by_coinbase_height` in `stackslib/src/chainstate/nakamoto/mod.rs`: [1](#0-0) 

it first calls `conn.get_nakamoto_block_id_at_coinbase_height(tip_index_hash, coinbase_height)`, and if that fails, falls back to `conn.get_ancestor_block_id(coinbase_height, tip_index_hash)`. Both of these take `tip_index_hash` as an explicit argument, meaning the lookup is *parameterized by the specific tip*, not a global/unscoped index. This is the `StacksDBIndexed` trait interface, which is implemented against the MARF-indexed header table — ancestor lookups at a given height are inherently fork-scoped because they walk the ancestor chain (or use a fork-specific index keyed by the tip) rather than doing a global height-based lookup. This is the same general mechanism used by `get_tip_ancestor` in `get_scheduled_block_rewards_in_fork_at_height`: [2](#0-1) 

Once `get_header_by_coinbase_height` correctly resolves `matured_tenure_block_header` to the unique ancestor of `tip_index_hash`, `get_matured_miner_reward_schedules` then queries `get_scheduled_block_rewards_at_block` using `matured_tenure_block_header.index_block_hash()`: [3](#0-2) 

`index_block_hash` is a hash of `(consensus_hash, block_hash)`, and is by construction globally unique per block, not merely unique-per-fork. Since the *header itself* was already correctly resolved to be in `tip_index_hash`'s ancestry before its `index_block_hash()` is computed, querying the `payments` table by that exact `index_block_hash` in `get_scheduled_block_rewards_at_block`: [4](#0-3) 

cannot return a "wrong fork" row — there is no other block in the database with that same `index_block_hash`, because `index_block_hash` uniquely identifies one specific block regardless of fork. The premise that "two different forks can have blocks at the same `matured_coinbase_height` with different `index_block_hash`" is true, but irrelevant to this code path: the fork-selection happens entirely in `get_header_by_coinbase_height` (which is tip-scoped), and the subsequent by-`index_block_hash` lookup is exact-match, not height-based, so it cannot select the "wrong" one once the header lookup has already disambiguated the fork.

The same pattern holds for `get_parent_matured_miner`, which also calls `get_scheduled_block_rewards_at_block` using an `index_block_hash` constructed from `(parent_consensus_hash, parent_block_hash)` taken directly from the already-resolved, fork-correct `MinerPaymentSchedule` row: [5](#0-4) 

There is existing integration test coverage exercising exactly the maturity-window schedule lookup across many coinbase heights, confirming the values line up correctly under normal operation: [6](#0-5) 

I could not retrieve the exact body of `get_nakamoto_block_id_at_coinbase_height` / `get_ancestor_block_id` implementations (only their call sites and signatures were indexed), so I cannot 100% rule out a bug *inside* that fork-walking logic itself. However, that is a different, unproven claim than the one in the question, which asserts that the *by-`index_block_hash`* lookup in `get_scheduled_block_rewards_at_block` is the vulnerable step due to lack of a "fork-membership check" — and that claim is false, because `index_block_hash` is a globally unique key, and the fork-membership determination has already been made upstream by the coinbase-height/ancestor resolution before this key is even constructed. Because reorg-driven fork walking is exactly what `get_header_by_coinbase_height`'s `tip_index_hash`-scoped ancestor lookup is designed to prevent, and no code path bypasses this scoping before reaching the unique-key lookup, the described attack does not have a valid trigger path under an unprivileged attacker.

### No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2991-3024)
```rust
    pub fn get_header_by_coinbase_height<SDBI: StacksDBIndexed>(
        conn: &mut SDBI,
        tip_index_hash: &StacksBlockId,
        coinbase_height: u64,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        // nakamoto block?
        if let Some(block_id) =
            conn.get_nakamoto_block_id_at_coinbase_height(tip_index_hash, coinbase_height)?
        {
            return Self::get_block_header_nakamoto(conn.sqlite(), &block_id);
        }

        // epoch2 block?
        let Some(ancestor_at_height) = conn
            .get_ancestor_block_id(coinbase_height, tip_index_hash)?
            .map(|ancestor| Self::get_block_header(conn.sqlite(), &ancestor))
            .transpose()?
            .flatten()
        else {
            warn!("No such epoch2 ancestor";
                  "coinbase_height" => coinbase_height,
                  "tip_index_hash" => %tip_index_hash,
            );
            return Ok(None);
        };
        // only return if it is an epoch-2 block, because that's
        // the only case where block_height can be interpreted as
        // tenure height.
        if ancestor_at_height.is_epoch_2_block() {
            return Ok(Some(ancestor_at_height));
        }

        Ok(None)
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L706-718)
```rust
    /// Get the scheduled miner rewards at a particular index hash
    pub fn get_scheduled_block_rewards_at_block(
        conn: &DBConn,
        index_block_hash: &StacksBlockId,
    ) -> Result<Vec<MinerPaymentSchedule>, Error> {
        let qry =
            "SELECT * FROM payments WHERE index_block_hash = ?1 ORDER BY vtxindex ASC".to_string();
        let args = params![index_block_hash];
        let rows =
            query_rows::<MinerPaymentSchedule, _>(conn, &qry, args).map_err(Error::DBError)?;
        test_debug!("{} rewards in {}", rows.len(), index_block_hash);
        Ok(rows)
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L720-747)
```rust
    /// Get the scheduled miner rewards in a particular Stacks fork at a particular height.
    pub fn get_scheduled_block_rewards_in_fork_at_height(
        tx: &mut StacksDBTx<'_>,
        tip: &StacksHeaderInfo,
        block_height: u64,
    ) -> Result<Vec<MinerPaymentSchedule>, Error> {
        let ancestor_info = match StacksChainState::get_tip_ancestor(tx, tip, block_height)? {
            Some(info) => info,
            None => {
                test_debug!("No ancestor at height {}", block_height);
                return Ok(vec![]);
            }
        };

        let qry = "SELECT * FROM payments WHERE block_hash = ?1 AND consensus_hash = ?2 ORDER BY vtxindex ASC".to_string();
        let args = params![
            ancestor_info.anchored_header.block_hash(),
            ancestor_info.consensus_hash,
        ];
        let rows = query_rows::<MinerPaymentSchedule, _>(tx, &qry, args).map_err(Error::DBError)?;
        test_debug!(
            "{} rewards in {}/{}",
            rows.len(),
            &ancestor_info.consensus_hash,
            &ancestor_info.anchored_header.block_hash()
        );
        Ok(rows)
    }
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

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L4692-4728)
```rust
    /// Given the list of matured miners, find the miner reward schedule that produced the parent
    /// of the block whose coinbase just matured.
    pub fn get_parent_matured_miner(
        conn: &DBConn,
        mainnet: bool,
        latest_matured_miners: &[MinerPaymentSchedule],
    ) -> Result<MinerPaymentSchedule, Error> {
        let parent_miner = if let Some(miner) = latest_matured_miners.first().as_ref() {
            StacksChainState::get_scheduled_block_rewards_at_block(
                conn,
                &StacksBlockHeader::make_index_block_hash(
                    &miner.parent_consensus_hash,
                    &miner.parent_block_hash,
                ),
            )?
            .pop()
            .unwrap_or_else(|| {
                if miner.parent_consensus_hash == FIRST_BURNCHAIN_CONSENSUS_HASH
                    && miner.parent_block_hash == FIRST_STACKS_BLOCK_HASH
                {
                    MinerPaymentSchedule::genesis(mainnet)
                } else {
                    panic!(
                        "CORRUPTION: parent {}/{} of {}/{} not found in DB",
                        &miner.parent_consensus_hash,
                        &miner.parent_block_hash,
                        &miner.consensus_hash,
                        &miner.block_hash
                    );
                }
            })
        } else {
            MinerPaymentSchedule::genesis(mainnet)
        };

        Ok(parent_miner)
    }
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/tests.rs (L2824-2840)
```rust
    // verify that matured miner records were in place
    let mut matured_rewards = vec![];
    {
        let chainstate = &mut peer.chain.stacks_node.as_mut().unwrap().chainstate;
        let sort_db = peer.chain.sortdb.as_mut().unwrap();
        let (mut chainstate_tx, _) = chainstate.chainstate_tx_begin();
        for i in 0..24 {
            let matured_reward_opt = NakamotoChainState::get_matured_miner_reward_schedules(
                &mut chainstate_tx,
                &tip.index_block_hash(),
                i,
            )
            .unwrap();
            matured_rewards.push(matured_reward_opt);
        }
    }
    for (i, matured_reward_opt) in matured_rewards[4..].into_iter().enumerate() {
```
