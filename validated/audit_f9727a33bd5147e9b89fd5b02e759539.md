### No vulnerability found for this question.

The premise—that `expected_burn` in `common_validate_against_burnchain` could differ across nodes due to a "race between block-commit broadcast and burn total tabulation"—does not correspond to a real code path in this codebase.

The equality being checked is `self.header.burn_spent == expected_burn`, where `expected_burn` comes from `NakamotoChainState::get_expected_burns`, which reads `burn_view_sn.total_burn` off a `BlockSnapshot` already persisted in the `SortitionDB` for a specific `consensus_hash` [1](#0-0) . That stored `total_burn` value is not "tabulated" live or subject to any broadcast-timing race — it is computed exactly once, deterministically, inside `BlockSnapshot::make_snapshot_in_epoch` (via `BurnchainStateTransition::from_block_ops`) as a pure function of the full, already-Bitcoin-confirmed set of block-commit ops belonging to that specific burnchain block [2](#0-1) [3](#0-2) . This computation happens only after the burnchain indexer has already required its confirmation threshold (`stable_confirmations`) on the underlying Bitcoin block, so any block-commit that hasn't yet made it into a confirmed block simply isn't part of that block's op set on any node — there is no partial/live "burn total" state that could be observed mid-tabulation.

Once written, the snapshot's `total_burn` for a given `sortition_id`/`consensus_hash` is immutable and identical on every node that has processed the same confirmed Bitcoin block, since it is derived purely from that block's contents (which are, by definition, already finalized on the Bitcoin chain by the time any node processes them) [4](#0-3) . `common_validate_against_burnchain` and `validate_normal_against_burnchain` only ever compare against this already-committed, deterministic value [5](#0-4) , and the tests confirm this value is stable/deterministic across snapshot construction and re-validation [6](#0-5) .

Since there is no mechanism in this repo by which two honest nodes, having processed the same confirmed Bitcoin block, could compute or observe different `total_burn`/`expected_burn` values for the same `consensus_hash`, the claimed 1-satoshi divergence and resulting chain split has no reachable code path.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1881-1895)
```rust
        // this block must commit to all of the work seen so far
        if let Some(expected_burn) = expected_burn {
            if self.header.burn_spent != expected_burn {
                warn!("Invalid Nakamoto block header: invalid total burns";
                    "header.burn_spent" => self.header.burn_spent,
                    "expected_burn" => expected_burn,
                    "consensus_hash" => %self.header.consensus_hash,
                    "stacks_block_hash" => %self.header.block_hash(),
                    "stacks_block_id" => %self.header.block_id()
                );
                return Err(ChainstateError::InvalidStacksBlock(
                    "Invalid Nakamoto block: invalid total burns".into(),
                ));
            }
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2625-2648)
```rust
    pub(crate) fn get_expected_burns<SH: SortitionHandle>(
        sort_handle: &SH,
        chainstate_conn: &Connection,
        block: &NakamotoBlock,
    ) -> Result<Option<u64>, ChainstateError> {
        let burn_view_ch = if let Some(tenure_payload) = block.get_tenure_tx_payload() {
            &tenure_payload.burn_view_consensus_hash
        } else {
            // if there's no new tenure for this block, the burn total should be the same as its parent
            let parent_burns_opt =
                Self::get_block_header(chainstate_conn, &block.header.parent_block_id)?
                    .map(|parent| parent.anchored_header.total_burns());
            return Ok(parent_burns_opt);
        };
        let burn_view_sn =
            SortitionDB::get_block_snapshot_consensus(sort_handle.sqlite(), burn_view_ch)?
                .ok_or_else(|| {
                    warn!("Could not load expected burns -- no such burn view";
                          "burn_view_consensus_hash" => %burn_view_ch
                    );
                    ChainstateError::NoSuchBlockError
                })?;
        Ok(Some(burn_view_sn.total_burn))
    }
```

**File:** stackslib/src/chainstate/burn/db/processing.rs (L120-157)
```rust
    ) -> Result<(BlockSnapshot, BurnchainStateTransition), BurnchainError> {
        let this_block_height = block_header.block_height;
        let this_block_hash = block_header.block_hash.clone();

        // make the burn distribution, and in doing so, identify the user burns that we'll keep
        let state_transition = BurnchainStateTransition::from_block_ops(self, burnchain, parent_snapshot, this_block_ops, missed_commits)
            .map_err(|e| {
                error!("TRANSACTION ABORTED when converting {} blockstack operations in block {} ({}) to a burn distribution: {:?}", this_block_ops.len(), this_block_height, &this_block_hash, e);
                e
            })?;

        let next_pox = SortitionDB::make_next_pox_id(parent_pox.clone(), next_pox_info.as_ref());
        let next_sortition_id = SortitionDB::make_next_sortition_id(
            parent_pox,
            &this_block_hash,
            next_pox_info.as_ref(),
        );

        // do the cryptographic sortition and pick the next winning block.
        let mut snapshot = BlockSnapshot::make_snapshot(
            mainnet,
            self,
            burnchain,
            &next_sortition_id,
            &next_pox,
            parent_snapshot,
            block_header,
            &state_transition,
            initial_mining_bonus_ustx,
        )
        .map_err(|e| {
            error!(
                "TRANSACTION ABORTED when taking snapshot at block {} ({}): {:?}",
                this_block_height, &this_block_hash, e
            );
            BurnchainError::DBError(e)
        })?;

```

**File:** stackslib/src/chainstate/burn/sortition.rs (L628-638)
```rust
        // total burn.  If this ever overflows, then just stall the chain and deny all future
        // sortitions (at least the chain will remain available to serve queries, but it won't be
        // able to make progress).
        let next_burn_total = match last_burn_total.checked_add(block_burn_total) {
            Some(new_total) => new_total,
            None => {
                // overflow.  Deny future sortitions
                warn!("Cumulative sortition burn has overflown.  Subsequent sortitions will be denied.");
                return make_snapshot_no_sortition();
            }
        };
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L740-768)
```rust
        Ok(BlockSnapshot {
            block_height,
            burn_header_hash: block_hash,
            burn_header_timestamp: block_header.timestamp,
            parent_burn_header_hash: parent_block_hash,
            consensus_hash: next_ch,
            ops_hash: next_ops_hash,
            total_burn: next_burn_total,
            sortition: true,
            sortition_hash: final_sortition_hash,
            winning_block_txid: winning_block.txid,
            winning_stacks_block_hash: winning_block.block_header_hash,
            index_root: TrieHash::EMPTY, // will be overwritten,
            num_sortitions: parent_snapshot.num_sortitions + 1,
            stacks_block_accepted: false,
            stacks_block_height: 0,
            arrival_index: 0,
            canonical_stacks_tip_height: parent_snapshot.canonical_stacks_tip_height,
            canonical_stacks_tip_hash: parent_snapshot.canonical_stacks_tip_hash.clone(),
            canonical_stacks_tip_consensus_hash: parent_snapshot
                .canonical_stacks_tip_consensus_hash
                .clone(),
            sortition_id: my_sortition_id.clone(),
            parent_sortition_id: parent_snapshot.sortition_id.clone(),
            pox_valid: true,
            accumulated_coinbase_ustx,
            miner_pk_hash,
        })
    }
```

**File:** stackslib/src/burnchains/tests/burnchain.rs (L866-893)
```rust
        if i > 0 {
            expected_burn_total += i as u64;

            assert_eq!(snapshot.total_burn, expected_burn_total);
            assert_eq!(
                snapshot.winning_block_txid,
                Txid::from_bytes(&[
                    i, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, i
                ])
                .unwrap()
            );
            assert_eq!(
                snapshot.winning_stacks_block_hash,
                BlockHeaderHash::from_bytes(&[
                    i, i, i, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0
                ])
                .unwrap()
            );
            assert_eq!(snapshot.burn_header_hash, burn_block_hash);
            assert_eq!(snapshot.parent_burn_header_hash, parent_burn_block_hash);
            assert_eq!(snapshot.block_height, (i as u64) + 1 + first_block_height);
            assert!(snapshot.sortition);
        } else {
            assert!(!snapshot.sortition);
            assert_eq!(snapshot.total_burn, 0);
        }
```
