No vulnerability found for this question.

**Analysis:** The claimed race does not exist because burnchain block processing is strictly sequential and each block's writes are fully committed before the next block is checked.

- `Burnchain::filter_block_VRF_dups` only dedupes within one block's `checked_ops`, as described [1](#0-0) , but cross-block duplicate detection is handled separately by `LeaderKeyRegisterOp::check` via `tx.has_VRF_public_key(&self.public_key)` [2](#0-1) .
- `has_VRF_public_key` reads the MARF-indexed `vrf_key_status` key at `self.context.chain_tip`, i.e., the parent sortition tip of the block currently being processed [3](#0-2) .
- The coordinator (both epoch2x and Nakamoto) finds unprocessed burn blocks and processes them strictly in ancestor order, one block at a time, via `find_sortitions_to_process`/`handle_new_nakamoto_burnchain_block`, walking a `VecDeque` and requiring the parent to already be marked processed before its child is handled [4](#0-3) .
- Each block's checked ops (including `LeaderKeyRegisterOp::check`) and the corresponding MARF write (`append_chain_tip_snapshot`, which persists `vrf_key_status`) happen within the same processing step for that block, and this step fully commits (`tx.commit()`) before the next block in the queue is processed [5](#0-4) .
- The existing unit test `has_VRF_public_key` explicitly demonstrates that once a `LeaderKeyRegisterOp` is committed in one burn block, `has_VRF_public_key` correctly returns `true` for a chain tip built on top of that block, confirming persistence is visible before any subsequent block is checked [6](#0-5) .
- There is no code path where two sequential burn blocks are checked concurrently or where block H+1's `check()` runs against a `SortitionHandleTx` that hasn't yet observed block H's committed leader-key write — processing is single-threaded and ordered, so no "race" window exists as hypothesized in the question.

Given this, submitting a duplicate-pubkey `LeaderKeyRegisterOp` at height H+1 after one was accepted at height H would be rejected deterministically with `op_error::LeaderKeyAlreadyRegistered`, matching the existing test fixture behavior [7](#0-6) . The equality "leader key consumed by an accepted block-commit == a key registered exactly once and never reused" holds both before and after tracing the code, so there is no exploitable divergence.

### Citations

**File:** stackslib/src/burnchains/burnchain.rs (L1042-1075)
```rust
    /// Verify that there are no duplicate VRF keys registered.
    /// If a key was registered more than once, take the first one and drop the rest.
    /// checked_ops must be sorted by vtxindex
    /// Returns the filtered list of blockstack ops
    pub fn filter_block_VRF_dups(
        mut checked_ops: Vec<BlockstackOperationType>,
    ) -> Vec<BlockstackOperationType> {
        debug!("Check Blockstack transactions: reject duplicate VRF keys");
        assert!(Burnchain::ops_are_sorted(&checked_ops));

        let mut all_keys: HashSet<VRFPublicKey> = HashSet::new();
        checked_ops.retain(|op| {
            if let BlockstackOperationType::LeaderKeyRegister(data) = op {
                if all_keys.contains(&data.public_key) {
                    // duplicate
                    warn!(
                        "REJECTED({}) leader key register {} at {},{}: Duplicate VRF key",
                        data.block_height, &data.txid, data.block_height, data.vtxindex;
                        "consensus_hash" => %data.consensus_hash
                    );
                    false
                } else {
                    // first case
                    all_keys.insert(data.public_key.clone());
                    true
                }
            } else {
                // preserve
                true
            }
        });

        checked_ops
    }
```

**File:** stackslib/src/chainstate/burn/operations/leader_key_register.rs (L213-234)
```rust
    pub fn check(
        &self,
        _burnchain: &Burnchain,
        tx: &mut SortitionHandleTx,
    ) -> Result<(), op_error> {
        /////////////////////////////////////////////////////////////////
        // Keys must be unique -- no one can register the same key twice
        /////////////////////////////////////////////////////////////////

        // key selected here must never have been submitted on this fork before
        let has_key_already = tx.has_VRF_public_key(&self.public_key)?;

        if has_key_already {
            warn!(
                "Invalid leader key registration: public key {} previously used",
                &self.public_key.to_hex()
            );
            return Err(op_error::LeaderKeyAlreadyRegistered);
        }

        Ok(())
    }
```

**File:** stackslib/src/chainstate/burn/operations/leader_key_register.rs (L609-637)
```rust
        let check_fixtures = vec![
            CheckFixture {
                // reject -- key already registered
                op: LeaderKeyRegisterOp {
                    consensus_hash: ConsensusHash::from_bytes(
                        &hex_bytes("0000000000000000000000000000000000000000").unwrap(),
                    )
                    .unwrap(),
                    public_key: VRFPublicKey::from_bytes(
                        &hex_bytes(
                            "a366b51292bef4edd64063d9145c617fec373bceb0758e98cd72becd84d54c7a",
                        )
                        .unwrap(),
                    )
                    .unwrap(),
                    memo: vec![1, 2, 3, 4, 5],

                    txid: Txid::from_bytes_be(
                        &hex_bytes(
                            "1bfa831b5fc56c858198acb8e77e5863c1e9d8ac26d49ddb914e24d8d4083562",
                        )
                        .unwrap(),
                    )
                    .unwrap(),
                    vtxindex: 455,
                    block_height: 123,
                    burn_header_hash: block_123_hash.clone(),
                },
                res: Err(op_error::LeaderKeyAlreadyRegistered),
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L1489-1495)
```rust
    pub fn has_VRF_public_key(&mut self, key: &VRFPublicKey) -> Result<bool, db_error> {
        let chain_tip = self.context.chain_tip.clone();
        let key_status = self
            .get_indexed(&chain_tip, &db_keys::vrf_key_status(key))?
            .is_some();
        Ok(key_status)
    }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L7987-8010)
```rust
        let mut db = SortitionDB::connect_test(block_height, &first_burn_hash).unwrap();

        let no_key_snapshot = test_append_snapshot(&mut db, BurnchainHeaderHash([0x01; 32]), &[]);

        let has_key_before = {
            let mut ic = SortitionHandleTx::begin(&mut db, &no_key_snapshot.sortition_id).unwrap();
            ic.has_VRF_public_key(&public_key).unwrap()
        };

        assert!(!has_key_before);

        let key_snapshot = test_append_snapshot(
            &mut db,
            BurnchainHeaderHash([0x03; 32]),
            &[BlockstackOperationType::LeaderKeyRegister(leader_key)],
        );

        let has_key_after = {
            let mut ic = SortitionHandleTx::begin(&mut db, &key_snapshot.sortition_id).unwrap();
            ic.has_VRF_public_key(&public_key).unwrap()
        };

        assert!(has_key_after);
    }
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L1066-1103)
```rust
    /// Find sortitions to process.
    /// Returns the last processed ancestor of `cursor`, and any unprocessed burnchain blocks
    fn find_sortitions_to_process(
        &self,
        mut cursor: BurnchainHeaderHash,
    ) -> Result<(SortitionId, VecDeque<BurnchainBlockData>), Error> {
        let mut sortitions_to_process = VecDeque::new();
        let last_processed_ancestor = loop {
            if let Some(found_sortition) = self.sortition_db.is_sortition_processed(&cursor)? {
                debug!(
                    "Ancestor sortition {} of block {} is processed",
                    &found_sortition, &cursor
                );
                break found_sortition;
            }

            let current_block =
                BurnchainDB::get_burnchain_block(self.burnchain_blocks_db.conn(), &cursor)
                    .map_err(|e| {
                        warn!(
                            "ChainsCoordinator: could not retrieve block burnhash={}",
                            &cursor
                        );
                        Error::NonContiguousBurnchainBlock(e)
                    })?;

            debug!(
                "Unprocessed block: ({}, {})",
                &current_block.header.block_hash.to_string(),
                current_block.header.block_height
            );

            let parent = current_block.header.parent_block_hash.clone();
            sortitions_to_process.push_front(current_block);
            cursor = parent;
        };
        Ok((last_processed_ancestor, sortitions_to_process))
    }
```

**File:** stackslib/src/chainstate/burn/db/processing.rs (L196-223)
```rust
        // store the snapshot
        let index_root = self.append_chain_tip_snapshot(
            parent_snapshot,
            &snapshot,
            &state_transition.accepted_ops,
            missed_commits,
            next_pox_info,
            reward_info,
            initialize_bonus,
        )?;

        snapshot.index_root = index_root;

        debug!("OPS-HASH({}): {}", this_block_height, &snapshot.ops_hash);
        debug!(
            "INDEX-ROOT({}): {}",
            this_block_height, &snapshot.index_root
        );
        debug!(
            "SORTITION-HASH({}): {}",
            this_block_height, &snapshot.sortition_hash
        );
        info!(
            "CONSENSUS({}): {}",
            this_block_height, &snapshot.consensus_hash
        );
        Ok((snapshot, state_transition))
    }
```
