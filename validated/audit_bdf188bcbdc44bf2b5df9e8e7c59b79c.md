### Title
`handle_poison_microblock` slashes a miner's coinbase for two same-signer microblock headers with no sequence-equality or fork check - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::handle_poison_microblock` (and its helper `check_microblock_header_signer`) only verifies that the two supplied `StacksMicroblockHeader`s recover to the same public-key hash; it never checks `mblock_header_1.sequence == mblock_header_2.sequence`, `prev_block` equality, or that the two headers actually conflict/represent a fork. The only place in the codebase that enforces "headers must actually conflict" (`sequence`, `prev_block`, `version` equality) is the *mempool admission* check in `stackslib/src/chainstate/stacks/db/blocks.rs` (`will_admit_mempool_tx`, lines 6844-6868), which is not part of consensus-level transaction processing.

### Finding Description
The broken equality: reward-slashing should occur **iff** two headers are a genuine double-sign (same `sequence`, conflicting content, same signer). The code instead requires only "same signer key," as shown here: [1](#0-0) 

`handle_poison_microblock` calls only this check before recording the poison report and uses `mblock_header_1.sequence` unconditionally as the "fork point": [2](#0-1) [3](#0-2) 

Nowhere in this function (nor in `process_transaction_payload`'s `PoisonMicroblock` arm) is there a check that `mblock_header_1.sequence == mblock_header_2.sequence`, that `prev_block` matches, or that the headers are distinct/conflicting blocks: [4](#0-3) 

The equivalent conflict check *does* exist, but only in the mempool gate `will_admit_mempool_tx`: [5](#0-4) 

That mempool check protects relay/admission into other nodes' mempools, but it is not invoked by `process_transaction` / `process_transaction_payload`, which is the actual consensus state-transition path executed when a block is validated/appended (`append_block` → `process_transaction` → `process_transaction_payload` → `run_poison_microblock` → `handle_poison_microblock`). A miner (an allowed, unprivileged actor per the threat model — "single miner slot") can therefore place a hand-crafted `PoisonMicroblock(h_i, h_j)` transaction directly into their own block, bypassing the mempool's `will_admit_mempool_tx` gate entirely, using two headers `h_i`, `h_j` from a legitimately-mined, non-forked linear microblock stream produced by some other miner M with pubkey hash `pubkh`.

Exploit flow:
1. Miner M mines microblocks with sequences 0..N under `pubkh`, no fork, `prev_block` chained legitimately.
2. Attacker (a miner) picks any two of M's real headers `h_i`, `h_j` (`i != j`), both correctly signed by M's key, not conflicting.
3. Attacker builds `TransactionPayload::PoisonMicroblock(h_i, h_j)`, includes it directly in a block they mine (bypassing mempool admission).
4. On block processing, `check_microblock_header_signer` succeeds because both recover to `pubkh`.
5. `handle_poison_microblock` looks up `pubkh`'s registration height, and unconditionally calls `insert_microblock_poison(height, attacker, h_i.sequence)` (or the lower of `h_i.sequence`/existing report), recording a poison entry despite no actual fork.
6. When M's coinbase for that tenure matures, the reward-maturation/slashing logic (`get_poison_microblock_report`/`find_mature_miner_rewards`) reroutes M's coinbase to the attacker.

This exploits the fact that `check_microblock_header_signer` verifies signer identity but not header conflict, and that consensus-level transaction processing has no independent re-validation of header conflict (unlike the mempool gate).

### Impact Explanation
This is block-reward theft: the legitimate miner M's entire tenure coinbase is redirected to an unprivileged attacker who never mined a fork and never possesses M's key — only two of M's broadcast/legitimate headers, which are public data (present in the microblock stream). This matches the "Critical: block-reward theft/double-payment/loss" category. It is repeatable against any miner whose microblock headers are observable (which is by design, since microblocks are broadcast), and requires no majority stake, no signer collusion, and no compromise of M's key.

### Likelihood Explanation
Preconditions: attacker needs (a) to be a miner able to include a self-crafted transaction directly in one of their own blocks (skipping the mempool gate), and (b) access to two headers from any other miner's real, matured (or not-yet-matured, within `MINER_REWARD_MATURITY`) microblock pubkey-hash registration. Both preconditions are trivially satisfiable by any single miner slot with minimal BTC stake, since microblock headers are broadcast publicly and inclusion of one's own crafted transaction in one's own block does not require going through another node's mempool validation. The attack is fully repeatable across tenures and miners.

### Recommendation
In `handle_poison_microblock` (or earlier, in `process_transaction_payload`'s `PoisonMicroblock` arm), before treating the transaction as a valid equivocation proof, enforce the same "actually conflicting" check that `will_admit_mempool_tx` performs: require `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, `mblock_header_1.version == mblock_header_2.version`, and that the two headers are not identical (differ in `tx_merkle_root`/`signature`, i.e., a genuine double-sign). This mirrors `MemPoolRejection::PoisonMicroblocksDoNotConflict` logic and must be applied at the consensus layer, not only at mempool admission.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/db/transactions.rs` test module, alongside `process_poison_microblock_same_block`):
1. Register a miner pubkey hash `block_pubkh` via `insert_microblock_pubkey_hash`.
2. Build a legitimate, non-forked microblock chain using `make_signed_microblock` with strictly increasing `sequence` (e.g., seq 5 and seq 6) and correctly chained `prev_block` hashes (seq 6's `prev_block` = seq 5's `block_hash()`), both signed by `block_privk`.
3. Construct `TransactionPayload::PoisonMicroblock(mblock_seq5.header, mblock_seq6.header)` — two non-conflicting, non-equal-sequence, correctly-linked headers.
4. Sign as `reporter_privk` and call `StacksChainState::process_transaction`.
5. Assert on both sides of the equality:
   - Before: `get_poison_microblock_report` returns `None` for `block_pubkh`'s height; M's projected coinbase reward is intact.
   - After: assert `process_transaction` returns `Ok(...)` (i.e., is incorrectly accepted) and `get_poison_microblock_report` now returns `Some((reporter_addr, 5))` despite no double-sign having occurred — demonstrating the reward-diversion path is triggered for a non-forked stream.
6. Contrast with `will_admit_mempool_tx` on the same payload, which should return `Err(MemPoolRejection::PoisonMicroblocksDoNotConflict)` — showing the consensus path (`process_transaction`) is more permissive than the mempool gate, confirming the gap.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L684-713)
```rust
    /// Given two microblock headers, were they signed by the same key?
    /// Return the pubkey hash if so; return Err otherwise
    fn check_microblock_header_signer(
        mblock_hdr_1: &StacksMicroblockHeader,
        mblock_hdr_2: &StacksMicroblockHeader,
    ) -> Result<Hash160, Error> {
        let pkh1 = mblock_hdr_1.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        let pkh2 = mblock_hdr_2.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        if pkh1 != pkh2 {
            let msg = format!(
                "Invalid PoisonMicroblock transaction -- signature pubkey hash {} != {}",
                &pkh1, &pkh2
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        Ok(pkh1)
    }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-756)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-856)
```rust
        // add punishment / commission record, if one does not already exist at lower sequence
        let (reporter_principal, reported_seq) = if let Some((reporter, seq)) = env
            .global_context
            .database
            .get_microblock_poison_report(mblock_pubk_height)?
        {
            // account for report loaded
            env.add_memory(u64::from(TypeSignature::PrincipalType.size().map_err(
                |_| Error::Expects("Failed to get size of PrincipalType".into()),
            )?))
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            // u128 sequence
            env.add_memory(16)
                .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            if mblock_header_1.sequence < seq {
                // this sender reports a point lower in the stream where a fork occurred, and is now
                // entitled to a commission of the punished miner's coinbase
                debug!("Sender {} reports a better poison-miroblock record (at {}) for key {} at height {} than {} (at {})", &sender_principal, mblock_header_1.sequence, &pubkh, mblock_pubk_height, &reporter, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                env.global_context.database.insert_microblock_poison(
                    mblock_pubk_height,
                    &sender_principal,
                    mblock_header_1.sequence,
                )?;
                (sender_principal, mblock_header_1.sequence)
            } else {
                // someone else beat the sender to this report
                debug!("Sender {} reports an equal or worse poison-microblock record (at {}, but already have one for {}); dropping...", &sender_principal, mblock_header_1.sequence, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                (reporter, seq)
            }
        } else {
            // first-ever report of a fork
            debug!(
                "Sender {} reports a poison-microblock record at seq {} for key {} at height {}",
                &sender_principal, mblock_header_1.sequence, &pubkh, &mblock_pubk_height;
                "sender" => %sender_principal,
                "microblock_pubkey_hash" => %pubkh
            );
            env.global_context.database.insert_microblock_poison(
                mblock_pubk_height,
                &sender_principal,
                mblock_header_1.sequence,
            )?;
            (sender_principal, mblock_header_1.sequence)
        };
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1389-1413)
```rust
            TransactionPayload::PoisonMicroblock(ref mblock_header_1, ref mblock_header_2) => {
                // post-conditions are not allowed for this variant, since they're non-sensical.
                // Their presence in this variant makes the transaction invalid.
                if !tx.post_conditions.is_empty() {
                    let msg = "Invalid Stacks transaction: PoisonMicroblock transactions do not support post-conditions".to_string();
                    info!("{}", &msg);

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }

                let cost_before = clarity_tx.cost_so_far();
                let res = clarity_tx.run_poison_microblock(
                    &origin_account.principal,
                    mblock_header_1,
                    mblock_header_2,
                )?;
                let mut cost = clarity_tx.cost_so_far();
                cost.sub(&cost_before)
                    .expect("BUG: running poison microblock tx has negative cost");

                let receipt =
                    StacksTransactionReceipt::from_poison_microblock(tx.clone(), res, cost);

                Ok(receipt)
            }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6868)
```rust
            TransactionPayload::PoisonMicroblock(microblock_header_1, microblock_header_2) => {
                if microblock_header_1.sequence != microblock_header_2.sequence
                    || microblock_header_1.prev_block != microblock_header_2.prev_block
                    || microblock_header_1.version != microblock_header_2.version
                {
                    return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict);
                }

                let microblock_pkh_1 = microblock_header_1
                    .check_recover_pubkey()
                    .map_err(|_e| MemPoolRejection::InvalidMicroblocks)?;
                let microblock_pkh_2 = microblock_header_2
                    .check_recover_pubkey()
                    .map_err(|_e| MemPoolRejection::InvalidMicroblocks)?;

                if microblock_pkh_1 != microblock_pkh_2 {
                    return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict);
                }

                if !has_microblock_pubkey {
                    return Err(MemPoolRejection::NoAnchorBlockWithPubkeyHash(
                        microblock_pkh_1,
                    ));
                }
            }
```
