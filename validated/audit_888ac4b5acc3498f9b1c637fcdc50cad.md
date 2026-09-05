## Analysis

The Palmera bug class here is: an unprivileged actor observes a pending, unauthenticated identifier in a to-be-confirmed transaction and front-runs it, permanently "consuming" that identifier so the legitimate owner's later transaction reverts. In `stacks-core`, the exact analog exists in the **VRF leader-key registration uniqueness check**.

`LeaderKeyRegisterOp::check()` enforces that a given VRF public key can only ever be registered once per fork, with no binding to who submitted it: [1](#0-0) 

Within a single Bitcoin block, duplicates of the same VRF key across multiple ops are resolved purely by transaction order (`vtxindex`), keeping only the earliest: [2](#0-1) 

Since `LeaderKeyRegisterOp` payloads are carried in a plaintext Bitcoin `OP_RETURN` output, the VRF public key a miner intends to register is visible in the Bitcoin mempool before confirmation: [3](#0-2) 

A subsequent `LeaderBlockCommitOp` does not re-check the VRF public key itself — it references a key purely by `(key_block_ptr, key_vtxindex)` coordinates, and if no key was recorded at that exact location (because it was dropped as a duplicate), the commit is rejected outright: [4](#0-3) 

This mirrors Palmera's `createRootSafe` bug precisely: the "identifier" (VRF public key) is unauthenticated, globally unique, and claimable by anyone who observes it first — permanently blocking the legitimate registrant.

### Title
Unprivileged front-running of VRF leader-key registration griefs honest miners' block-commit fees - (File: stackslib/src/chainstate/burn/operations/leader_key_register.rs)

### Summary
`LeaderKeyRegisterOp::check()` rejects any VRF public key that has already been seen on the current fork, with no check that the submitter of the *first* registration has any relationship to whoever originally intended to use that key. Because the VRF public key is transmitted in plaintext inside a Bitcoin `OP_RETURN` output, it is visible in the Bitcoin mempool before confirmation. Any unprivileged actor can copy an about-to-be-confirmed victim's VRF public key into their own `LeaderKeyRegisterOp` and get it mined with an earlier `vtxindex` in the same burn block (or in an earlier burn block), causing the victim's own registration to be silently dropped as a duplicate.

### Finding Description
`Burnchain::filter_block_VRF_dups` (stackslib/src/burnchains/burnchain.rs:1042-1075) keeps only the first-by-`vtxindex` `LeaderKeyRegisterOp` for any given VRF public key within a block, discarding the rest. `LeaderKeyRegisterOp::check` (stackslib/src/chainstate/burn/operations/leader_key_register.rs:213-234) additionally enforces global fork-wide uniqueness via `has_VRF_public_key`. Neither check ties the VRF public key to the Bitcoin sender/apparent identity that generated it — uniqueness is decided purely by "who got included first."

Since the VRF key is sent as unencrypted `OP_RETURN` payload data (built in `build_leader_key_register_tx`, stacks-node/src/burnchains/bitcoin_regtest_controller.rs:904-964), it is observable by anyone monitoring the Bitcoin mempool prior to confirmation — exactly the front-runnable condition described in the Palmera report (attacker sees the honest party's pending argument and races to consume it first).

If the attacker's copy of the key lands at an earlier `vtxindex` (same block) or an earlier block height, the victim's own registration op is dropped by the dedup logic and never gets written into the `leader_keys` table. The victim's node, however, still tracks its own submission by `txid` and will later build a `LeaderBlockCommitOp` pointing at the `(key_block_ptr, key_vtxindex)` coordinates it expected its key to occupy. `LeaderBlockCommitOp::check_common` (stackslib/src/chainstate/burn/operations/leader_block_commit.rs:1210-1232) performs `tx.get_leader_key_at(leader_key_block_height, self.key_vtxindex, &tx_tip)` and rejects the commit with `BlockCommitNoLeaderKey` if nothing is recorded there — which is exactly what happens once the victim's key registration was suppressed.

### Impact Explanation
This is a minority-triggerable, unprivileged VRF-registration poisoning attack: a single attacker with no special key or majority hashpower can grief a competing miner by consuming their about-to-be-registered VRF public key. The consequence is that the victim's subsequent `LeaderBlockCommitOp` — which already burns real BTC — is rejected as invalid because it cannot find its paired leader key, causing the victim to lose their committed burn fee with no chance to win the sortition for that round. This falls squarely under the High-severity bucket: "a minority-triggerable sortition/VRF/static-validation divergence, a poison or reward mis-payment bounded to fees."

### Likelihood Explanation
The attack only requires watching the Bitcoin mempool for `LeaderKeyRegisterOp` transactions (a routine, unprivileged, public activity) and submitting a competing transaction with the same VRF public key at a higher fee rate (or via replace-by-fee) to obtain an earlier position in the same or an earlier block. No special privileges, node-operator access, or majority hashpower are needed, making this practically reproducible by any Bitcoin-transacting actor who wants to grief specific miners.

### Recommendation
Bind VRF key uniqueness enforcement to the registering identity (e.g., require that the `apparent_sender`/Bitcoin input of the `LeaderKeyRegisterOp` match across duplicate detections, or otherwise ensure that a colliding registration from a different sender cannot invalidate another party's already-broadcast registration). Alternatively, allow `LeaderBlockCommitOp::check_common` to tolerate the "displaced" scenario by re-resolving the miner's originally-submitted key via `txid` rather than solely via `(key_block_ptr, key_vtxindex)`, so a benign collision does not automatically burn the honest miner's commit fee.

### Proof of Concept
1. Honest miner M broadcasts `LeaderKeyRegisterOp_M` containing VRF public key `K` to the Bitcoin mempool (as built in `build_leader_key_register_tx`).
2. Attacker A observes `LeaderKeyRegisterOp_M` in the mempool and extracts `K` from the plaintext `OP_RETURN` payload.
3. A crafts and broadcasts `LeaderKeyRegisterOp_A` with the identical VRF public key `K`, paying a higher fee (or using RBF) so it is mined with a lower `vtxindex` than M's transaction, either in the same burn block or an earlier one.
4. During burnchain block processing, `Burnchain::filter_block_VRF_dups` / `LeaderKeyRegisterOp::check` retains A's registration for `K` and drops M's, per stackslib/src/burnchains/burnchain.rs:1046-1075 and stackslib/src/chainstate/burn/operations/leader_key_register.rs:213-234.
5. M's node, unaware its own registration was dropped, later submits `LeaderBlockCommitOp_M` referencing the `(block_height, vtxindex)` coordinates it expected for its key.
6. `LeaderBlockCommitOp::check_common` finds no leader key at those coordinates and rejects the commit with `op_error::BlockCommitNoLeaderKey` (stackslib/src/chainstate/burn/operations/leader_block_commit.rs:1223-1232), causing M to lose the BTC burned in that commit transaction with zero chance of winning the sortition.

### Citations

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

**File:** stacks-node/src/burnchains/bitcoin_regtest_controller.rs (L920-940)
```rust

        // Serialize the payload
        let op_bytes = {
            let mut buffer = vec![];
            let mut magic_bytes = self.config.burnchain.magic_bytes.as_bytes().to_vec();
            buffer.append(&mut magic_bytes);
            payload
                .consensus_serialize(&mut buffer)
                .expect("FATAL: invalid operation");
            buffer
        };

        let consensus_output = TxOut {
            value: 0,
            script_pubkey: Builder::new()
                .push_opcode(opcodes::All::OP_RETURN)
                .push_slice(&op_bytes)
                .into_script(),
        };

        tx.output = vec![consensus_output];
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L1210-1232)
```rust
        /////////////////////////////////////////////////////////////////////////////////////
        // There must exist a previously-accepted key from a LeaderKeyRegister
        /////////////////////////////////////////////////////////////////////////////////////

        if leader_key_block_height >= self.block_height {
            warn!(
                "Invalid block commit: references leader key in the same or later block ({} >= {})",
                leader_key_block_height, self.block_height;
                "apparent_sender" => %apparent_sender_repr
            );
            return Err(op_error::BlockCommitNoLeaderKey);
        }

        let _register_key = tx
            .get_leader_key_at(leader_key_block_height, self.key_vtxindex.into(), &tx_tip)?
            .ok_or_else(|| {
                warn!(
                    "Invalid block commit: no corresponding leader key at {},{} in fork {}",
                    leader_key_block_height, self.key_vtxindex, &tx.context.chain_tip;
                    "apparent_sender" => %apparent_sender_repr
                );
                op_error::BlockCommitNoLeaderKey
            })?;
```
