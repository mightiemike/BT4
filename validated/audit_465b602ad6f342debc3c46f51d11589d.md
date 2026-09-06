### Title
`handle_poison_microblock` never enforces `mblock_header_1.prev_block == mblock_header_2.prev_block` (or `.version`), allowing unrelated, non-conflicting microblock headers signed by a reused key to be slashed as a "fork" - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::handle_poison_microblock` only checks that the two supplied headers were signed by the same key (`check_microblock_header_signer`) and that this key's pubkey hash is a recorded, not-yet-matured `microblock_pubkey_hash`. It never checks that the two headers share the same `prev_block` (or `version`), unlike the mempool-admission path (`MemPoolRejection` in `blocks.rs`), which does require `prev_block`/`version` equality before a poison tx is even allowed into the mempool. Because a miner's `microblock_pubkey_hash` is recorded per-key rather than per-tenure, an attacker can combine two genuinely-broadcast, non-conflicting microblock headers produced from two *different* honest tenures that happen to reuse the same key, at a colliding sequence number, and have them accepted as a valid double-sign proof by the block-processing path.

### Finding Description
The claimed broken equality is: `poison_reward_paid == genuine_previously_broadcast_double_sign_at_the_same_fork_point`.

`handle_poison_microblock` (stackslib/src/chainstate/stacks/db/transactions.rs:722-856) performs these checks and only these checks:
1. `check_microblock_header_signer(mblock_header_1, mblock_header_2)` — both headers recover to the same pubkey hash `pubkh` [1](#0-0) .
2. `pubkh` must be a previously-recorded `microblock_pubkey_hash` (`get_microblock_pubkey_hash_height`), i.e. some anchored block committed to using this key, and the recorded height must still be within the `MINER_REWARD_MATURITY` window [2](#0-1) .
3. It then records/updates the poison report keyed purely by `mblock_pubk_height` and `mblock_header_1.sequence`, with no comparison of `prev_block` or `version` between the two headers [3](#0-2) .

This is called directly from `process_transaction_payload` for `TransactionPayload::PoisonMicroblock`, with no additional structural checks performed before invoking Clarity's `run_poison_microblock` → `handle_poison_microblock` [4](#0-3) .

By contrast, the mempool-admission static check *does* require `prev_block`, `version`, and `sequence` equality before it will even accept a `PoisonMicroblock` tx into the mempool: [5](#0-4) . This proves the protocol's intended semantics for a *real* fork/equivocation require `prev_block` equality — the consensus-critical `handle_poison_microblock` simply omits this check.

Because `microblock_pubkey_hash` bookkeeping (`insert_microblock_pubkey_hash`/`get_microblock_pubkey_hash_height`) is keyed purely by the pubkey hash value (not by tenure/anchor-block context), if a miner reuses the same microblock signing key across two separate, non-forking, honestly-run tenures, an attacker can take one genuinely-broadcast header from tenure 1 and one genuinely-broadcast header from tenure 2 (different `prev_block`, unrelated chain contexts, but coincidentally-equal `sequence`), and submit them as a `PoisonMicroblock` transaction directly in a block (bypassing the stricter mempool relay check). `handle_poison_microblock` will accept this pair as valid proof of equivocation and pay/record a poison commission, even though no double-signing occurred at any single fork point.

### Impact Explanation
This results in a coinbase/commission mis-payment: the honest miner's block reward is partially destroyed and a fraction diverted to an arbitrary "reporter," even though the miner never equivocated at a real fork point. Per the stated severity mapping this is a "poison or reward mis-payment bounded to fees" — High, not a chain split, since it does not change block validity, sortition, or the MARF state root; it only misdirects/destroys a specific miner's coinbase reward.

### Likelihood Explanation
Preconditions: (1) a miner must reuse the same `microblock_pubkey_hash` across two different tenures — nothing in the recorded checks (`get_microblock_pubkey_hash_height`) prevents or flags this reuse; (2) both tenures' microblock streams must be observable/broadcast (which they are, by design, to the network) so an unprivileged attacker can collect one header from each; (3) the sequence numbers must collide, which is highly likely since sequences start at 0 and increment, so any tenure with more than a couple of microblocks will collide with another. The attacker needs no privileged access, no majority stake, and no forged signatures — only observation of two honest, real broadcasts plus the ability to submit a transaction (or self-mine a block containing it, bypassing mempool-level `prev_block` checks). This is fully repeatable against any miner who reuses a microblock key across tenures.

### Recommendation
Enforce `mblock_header_1.prev_block == mblock_header_2.prev_block` and `mblock_header_1.version == mblock_header_2.version` inside `handle_poison_microblock` itself (mirroring the check already present in the mempool's `MemPoolRejection::PoisonMicroblocksDoNotConflict` path), so that the consensus-critical path cannot be reached with headers from unrelated tenures/contexts. Additionally, consider making `microblock_pubkey_hash` bookkeeping tenure-scoped, or explicitly disallowing key reuse across tenures, to remove the underlying ambiguity.

### Proof of Concept
Rust integration test plan (two-tenure harness):
1. Have one miner run tenure A honestly, producing a single non-forking microblock stream signed with `microblock_pubkey_hash = K`, recorded via `insert_microblock_pubkey_hash` at height `h1`.
2. Have the same miner run tenure B honestly (different anchor block, different `prev_block` context), reusing the same key `K`, producing another single non-forking stream, recorded (or reusing the same record) for `K`.
3. Take a real header `h_A` from tenure A's stream and a real header `h_B` from tenure B's stream at the same `sequence` value (e.g., sequence 3 in both streams).
4. Construct `TransactionPayload::PoisonMicroblock(h_A.header, h_B.header)` and call `StacksChainState::process_transaction` directly (bypassing mempool `check_and_return`/`MemPoolRejection`), asserting:
   - **Before fix**: `process_transaction` returns `Ok(..)` and `get_poison_microblock_report` returns a report at `sequence == 3`, even though `h_A.header.prev_block != h_B.header.prev_block` — i.e. `poison_reward_paid == true` while `genuine_previously_broadcast_double_sign_at_same_fork_point == false`.
   - **After fix**: assert the call returns `Err(Error::InvalidStacksTransaction(..))` because `prev_block` mismatch is detected, restoring `poison_reward_paid == genuine_double_sign`.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L686-713)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L754-803)
```rust
        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();

        // for the microblock public key hash we had to process
        env.add_memory(20)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // for the block height we had to load
        env.add_memory(4)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // was the referenced public key hash used anytime in the past
        // MINER_REWARD_MATURITY blocks?
        let mblock_pubk_height = match microblock_height_opt {
            None => {
                // public key has never been seen before
                let msg = format!(
                    "Invalid Stacks transaction: microblock public key hash {} never seen in this fork",
                    &pubkh
                );
                warn!("{}", &msg;
                      "microblock_pubkey_hash" => %pubkh
                );

                return Err(Error::InvalidStacksTransaction(msg, false));
            }
            Some(height) => {
                if height
                    .checked_add(
                        u32::try_from(MINER_REWARD_MATURITY).expect("FATAL: maturity > 2^32"),
                    )
                    .expect("BUG: too many blocks")
                    < current_height
                {
                    let msg = format!(
                        "Invalid Stacks transaction: microblock public key hash from height {} has matured relative to current height {}",
                        height, current_height
                    );
                    warn!("{}", &msg;
                          "microblock_pubkey_hash" => %pubkh
                    );

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
                height
            }
        };
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L1389-1404)
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
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6861)
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
```
