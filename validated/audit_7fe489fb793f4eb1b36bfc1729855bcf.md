This confirms the key architectural fact: Clarity state reads (`get_data` on `PersistentWritableMarfStore`/`EphemeralMarfStore`) are all keyed by MARF path against `self.chain_tip`, which is fork-specific. `MARF::get` (via `get_by_key`/`get_path`) walks the trie chain rooted at the *tip currently open*, so a key like `microblock-poison::{height}` written in one fork's block only becomes reachable from that fork's own descendant blocks, not from a sibling fork's blocks that happen to share the same numeric height. This is exactly what `check_ancestor_block_hash`/`get_block_at_height` and the fork-isolation tests (`stackslib/src/clarity_vm/tests/ephemeral.rs`) demonstrate.### No vulnerability found for this question.

**Rationale:** `get_poison_microblock_report` reads via `clarity_tx.with_clarity_db_readonly(...).get_microblock_poison_report(height)`, which under the hood resolves to `ClarityBackingStore::get_data` on either `PersistentWritableMarfStore` or `EphemeralMarfStore`. Both implementations perform the lookup as `self.marf.get(&self.chain_tip, key)` [1](#0-0)  — i.e., the key `microblock-poison::{height}` is resolved by walking the MARF trie rooted at the *specific block* the `clarity_tx` is currently processing, not by a global/absolute-height index. MARF trie lookups are fork-scoped: `MARF::get_by_key`/`get_path` operate against the tip's own ancestor chain [2](#0-1) , and cross-fork ancestry checks (`check_ancestor_block_hash`) explicitly reject or diverge when a block hash isn't in the current tip's lineage [3](#0-2) . This fork isolation is exercised directly in `stackslib/src/clarity_vm/tests/ephemeral.rs`, which asserts that keys written under one block ID are invisible from a sibling/uncommitted branch and only visible along that block's own ancestry [4](#0-3) .

Consequently, when `find_mature_miner_rewards` calls `get_poison_microblock_report(clarity_tx, reward_height)` [5](#0-4) , the `clarity_tx` is always opened at the specific block/fork whose maturation is being processed. Even if fork A and fork B both compute the same numeric `reward_height = tip_stacks_height - MINER_REWARD_MATURITY`, a poison report inserted via `handle_poison_microblock`/`insert_microblock_poison` in a fork-A block is committed into fork A's own MARF trie path, which is unreachable from fork B's tip unless the poisoning block is a common ancestor shared by both forks (in which case the punishment legitimately applies to both, since they share that tenure's miner-punishment target). There is no code path by which a poison report filed against one fork's tenure can be read while evaluating a sibling fork's independent tenure at the same height — the claimed equality break (`reward recipient == honest miner's tenure-specific punishment target`) is preserved by MARF's fork-scoped storage/read model, not by any special-cased height check in `find_mature_miner_rewards` or `calculate_miner_reward`.

### Citations

**File:** stackslib/src/clarity_vm/database/marf.rs (L832-847)
```rust
    fn get_data(&mut self, key: &str) -> Result<Option<String>, VmExecutionError> {
        trace!("MarfedKV get: {:?} tip={}", key, &self.chain_tip);
        self.marf
            .get(&self.chain_tip, key)
            .or_else(|e| match e {
                Error::NotFoundError => {
                    trace!(
                        "MarfedKV get {:?} off of {:?}: not found",
                        key,
                        &self.chain_tip
                    );
                    Ok(None)
                }
                _ => Err(e),
            })
            .map_err(|_| VmInternalError::Expect("ERROR: Unexpected MARF Failure on GET".into()))?
```

**File:** stackslib/src/chainstate/stacks/index/marf.rs (L254-292)
```rust
    /// Check if a block can open successfully, i.e.,
    ///   it's a known block, the storage system isn't issueing IOErrors, _and_ it's in the same fork
    ///   as the current block
    /// The MARF _must_ be open to a valid block for this check to be evaluated.
    fn check_ancestor_block_hash(&mut self, bhh: &T) -> Result<(), Error> {
        self.with_conn(|conn| {
            let cur_block_hash = conn.get_cur_block();
            if cur_block_hash == *bhh {
                // a block is in its own fork
                return Ok(());
            }

            let bhh_height =
                MARF::get_block_height(conn, bhh, &cur_block_hash)?.ok_or_else(|| {
                    Error::NonMatchingForks(bhh.clone().to_bytes(), cur_block_hash.clone().to_bytes())
                })?;

            let actual_block_at_height = MARF::get_block_at_height(conn, bhh_height, &cur_block_hash)?
                .ok_or_else(|| Error::CorruptionError(format!(
                    "ERROR: Could not find block for height {}, but it was returned by MARF::get_block_height()", bhh_height)))?;

            if bhh != &actual_block_at_height {
                test_debug!("non-matching forks: {} != {}", bhh, &actual_block_at_height);
                return Err(Error::NonMatchingForks(
                    bhh.clone().to_bytes(),
                    cur_block_hash.to_bytes(),
                ));
            }

            // test open
            let result = conn.open_block(bhh);

            // restore
            conn.open_block(&cur_block_hash)
                .map_err(|e| Error::RestoreMarfBlockError(Box::new(e)))?;

            result
        })
    }
```

**File:** stackslib/src/chainstate/stacks/index/marf.rs (L1288-1313)
```rust
    /// Load up a MARF value by key, given a handle to the storage connection and a tip to work off
    /// of.
    pub fn get_by_key(
        storage: &mut TrieStorageConnection<T>,
        block_hash: &T,
        key: &str,
    ) -> Result<Option<MARFValue>, Error> {
        let (cur_block_hash, cur_block_id) = storage.get_cur_block_and_id();

        let path = TrieHash::from_key(key);

        let result = MARF::get_path(storage, block_hash, &path).or_else(|e| match e {
            Error::NotFoundError => Ok(None),
            _ => Err(e),
        });

        // restore
        storage
            .open_block_maybe_id(&cur_block_hash, cur_block_id)
            .inspect_err(|e| {
                warn!("Failed to re-open {cur_block_hash} {cur_block_id:?}: {e:?}");
                warn!("Result of failed key lookup '{key}': {result:?}");
            })?;

        result.map(|option_result| option_result.map(|leaf| leaf.data))
    }
```

**File:** stackslib/src/clarity_vm/tests/ephemeral.rs (L97-121)
```rust
    // verify all keys are present at the right chain tips
    for (i, block_id) in blocks.iter().enumerate() {
        debug!("readonly: open block #{}: {}", i, block_id);
        let mut marf_ro = marfed_kv.begin_read_only(Some(block_id));
        for j in 0..=i {
            // all values up to those inserted in the block with this ID are present
            let keys_and_values = &block_data[j];
            for (key, expected_value) in keys_and_values.iter() {
                let value = marf_ro.get_data(key).unwrap().unwrap();
                assert_eq!(expected_value, &value);
                debug!(
                    "readonly: at block #{} {}: {} == {}",
                    i, block_id, key, &value
                );
            }
        }
        for j in i + 1..blocks.len() {
            // all values afterwards are not present
            let keys_and_values = &block_data[j];
            for (key, _) in keys_and_values.iter() {
                assert!(marf_ro.get_data(key).unwrap().is_none());
                debug!("readonly: at block #{} {}: {} not mapped", i, block_id, key);
            }
        }
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1029-1031)
```rust
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);
```
