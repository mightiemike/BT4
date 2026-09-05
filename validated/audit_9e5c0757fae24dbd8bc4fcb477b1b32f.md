### Title
Poison-microblock validation never verifies the two headers actually conflict, letting an attacker duplicate a legitimate header to steal the miner's coinbase - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` (called from `handle_poison_microblock`) only verifies that `mblock_header_1` and `mblock_header_2` recover to the same public-key hash; it never checks that the two headers are distinct or actually conflict (e.g. same sequence/prev_block but different signature or tx_merkle_root). An attacker can therefore submit a `PoisonMicroblock` payload containing two bit-identical copies of a single, honestly-produced and already-broadcast microblock header, and the transaction will be accepted, causing `insert_microblock_poison` to record the attacker as the punisher and `calculate_miner_reward` to divert the honest miner's coinbase to the attacker.

### Finding Description
The broken equality is: **poison reward paid == exactly one valid double-signature event**. In practice, "poison reward paid" can now be triggered by a single microblock header duplicated into both payload slots.

Code path:
- `check_microblock_header_signer` (stackslib/src/chainstate/stacks/db/transactions.rs:686-713) recovers `pkh1` from `mblock_hdr_1` and `pkh2` from `mblock_hdr_2` and only checks `pkh1 != pkh2`. If the two headers are bit-identical, this trivially returns `Ok(pkh1)` — no check that `mblock_hdr_1 != mblock_hdr_2`, that their `block_hash()`s differ, or that they represent two distinct/competing microblocks. [1](#0-0) 
- `handle_poison_microblock` calls this signer check, then looks up `get_microblock_pubkey_hash_height(&pubkh)` and, provided the referenced pubkey hash is within `MINER_REWARD_MATURITY`, calls `env.global_context.database.insert_microblock_poison(mblock_pubk_height, &sender_principal, mblock_header_1.sequence)` to record the *attacker* (`sender_principal`) as the reporter for that miner's block height. [2](#0-1) 
- Later, `StacksChainState::calculate_miner_reward` (stackslib/src/chainstate/stacks/db/accounts.rs:804-980) reads the poison report via `get_poison_microblock_report`/`poison_reporter_opt`; if present, it redirects `coinbase_reward` (via `poison_microblock_commission`) to `reporter_address` and zeroes/`punished`-flags the actual miner's fees, regardless of whether a genuine fork ever existed. [3](#0-2) 

Existing guard analysis: the only other place checks are performed is the *mempool admission* path, `will_admit_mempool_tx` (stackslib/src/chainstate/stacks/db/blocks.rs:6844-6868), which requires `sequence`, `prev_block`, and `version` to match between the two headers (a necessary condition for a real fork) and requires `pkh1 == pkh2` and a known pubkey hash — but it likewise never rejects the case where the two headers are byte-for-byte identical (i.e., where no divergence, no second signature event, and no distinct `block_hash()` exist). This is only a mempool-admission heuristic, not a consensus check; the same gap exists in the actual consensus-critical processing path (`handle_poison_microblock`), so even if mempool admission were bypassed (e.g., by a miner directly including the crafted transaction in their own block, or as a mempool submission with an identical pair), `process_transaction` → `handle_poison_microblock` accepts it. [4](#0-3) 

Attacker's exact input: any honestly-signed microblock header `H` produced by a miner whose `microblock_pubkey_hash` was previously recorded (which happens unconditionally for every anchored block that declares a microblock public key, regardless of whether any fork occurs). The attacker crafts `TransactionPayload::PoisonMicroblock(H.clone(), H.clone())`, signs it under their own principal, and submits it as a normal transaction (or mines it into their own block if they hold a miner slot). Since `H == H`, `check_recover_pubkey()` on both returns the same `pkh`, and every downstream consistency check (`sequence`, `prev_block`, `version`, `pkh1==pkh2`) is trivially satisfied because they're comparing the identical struct to itself.

### Impact Explanation
This diverts block-reward funds: the honest miner's matured coinbase is truncated/zeroed (`punished = true`) and a slice of it (`poison_microblock_commission`) is paid to an address that never observed or reported an actual double-signed/forked microblock. This is a reward theft/misdirection bounded to the coinbase share (not fees only, since coinbase itself is redirected), which the rules classify as reward mis-payment. It does not, by itself, cause a chain split (all honest nodes evaluate the same transaction deterministically and reach the same wrong conclusion), so nodes stay in consensus but pay the wrong party — this is a real, deterministic, reward-diversion bug reachable by any single unprivileged participant who can observe one honestly-produced, already-public microblock header.

### Likelihood Explanation
Preconditions are minimal and cheap for an attacker: they need to observe one legitimately signed microblock header from any miner (these are broadcast/relayed data, not secret), reference its already-recorded `microblock_pubkey_hash` (recorded whenever any anchored block declares a microblock key — happens on essentially every tenure that streams microblocks), and be within the `MINER_REWARD_MATURITY` window relative to current height. No majority stake, no signer key, no additional BTC spend beyond a normal transaction fee is required — this is fully consistent with the "unprivileged, minority-triggerable" threat model. The attack is repeatable for every miner/tenure that publishes at least one microblock, as long as no other party has already filed an earlier (lower-sequence) poison report for the same pubkey hash (there's a first-mover "lower sequence wins" tie-break in `handle_poison_microblock`, but the attacker only needs sequence 0, the earliest possible, which is trivially available from the very first microblock in the stream).

### Recommendation
In `check_microblock_header_signer` (and/or in `handle_poison_microblock` before doing any DB writes), require the two headers to actually conflict: reject if `mblock_header_1.block_hash() == mblock_header_2.block_hash()` (or equivalently if the two headers are structurally identical, e.g. equal `signature`), in addition to the existing `sequence`/`prev_block` equality and pubkey-hash-equality checks already present in the mempool path. This distinguishes "same signer, same slot, different content" (a genuine equivocation) from "same signer, same slot, same header duplicated" (no equivocation at all). Apply the same duplicate-header rejection in the mempool's `will_admit_mempool_tx` check for defense in depth.

### Proof of Concept
Rust integration test plan (modeled closely on the existing `process_poison_microblock_transaction`/`process_poison_microblock_invalid_transaction` tests in stackslib/src/chainstate/stacks/db/transactions.rs):
1. Build a `TestChainstateBuilder` with a funded reporter address and a miner key `block_privk`.
2. Insert a real `microblock_pubkey_hash` for height 1 via `StacksChainState::insert_microblock_pubkey_hash(&mut conn, 1, &block_pubkh)` (simulating an honest miner's declared microblock key for a real tenure).
3. Construct exactly one legitimately signed microblock header `mblock = make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123)`.
4. Build `TransactionPayload::PoisonMicroblock(mblock.header.clone(), mblock.header.clone())` — i.e., duplicate the *same* header into both slots (assert `mblock.header == mblock.header` trivially, and that no second, distinct microblock was ever created).
5. Sign this as a `reporter_privk`-authored transaction and call `StacksChainState::process_transaction(&mut conn, &signed_tx_poison_microblock, false, None)`.
6. Assert both sides of the equality before/after:
   - Equality claimed by the protocol: "a poison reward is paid **iff** two headers with equal `(sequence, prev_block, pubkey_hash)` but different content/signature (i.e. `block_hash1 != block_hash2`) were submitted."
   - Before fix: assert `process_transaction` returns `Ok(..)` (not an error) and `StacksChainState::get_poison_microblock_report(&mut conn, 1)` returns `Some((reporter_addr, 123))`, even though only one microblock (`mblock`) ever existed — violating the equality.
   - Then mature the block past `MINER_REWARD_MATURITY` and assert, via `calculate_miner_reward`/`find_mature_miner_rewards`, that the resulting `MinerReward.coinbase` for `block_privk`'s address is reduced to `0` (punished) while the reporter's `MinerReward.coinbase` equals `StacksChainState::poison_microblock_commission(coinbase_reward)` — demonstrating reward diversion with no actual double-signature.
   - After applying the recommended fix (reject when `mblock_header_1.block_hash() == mblock_header_2.block_hash()`), assert `process_transaction` instead returns an `Err(Error::InvalidStacksTransaction(..))`, and the miner's coinbase remains unpunished.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-856)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-904)
```rust
        // process poison -- someone can steal a fraction of the total coinbase if they can present
        // evidence that the miner forked the microblock stream.  The remainder of the coinbase is
        // destroyed if this happens.
        let (child_address, child_recipient, coinbase_reward, punished) =
            if let Some(reporter_address) = poison_reporter_opt {
                if participant.miner {
                    // the poison-reporter, not the miner, gets a (fraction of the) reward
                    debug!(
                        "{:?} will recieve poison-microblock commission {}",
                        &reporter_address.to_string(),
                        StacksChainState::poison_microblock_commission(coinbase_reward)
                    );
                    (
                        reporter_address.clone(),
                        reporter_address.to_account_principal(),
                        StacksChainState::poison_microblock_commission(coinbase_reward),
                        true,
                    )
                } else {
                    // users that helped a miner that reported a poison-microblock get nothing
                    (
                        StacksAddress::burn_address(mainnet),
                        StacksAddress::burn_address(mainnet).to_account_principal(),
                        0,
                        false,
                    )
                }
            } else {
                // no poison microblock reported
                (
                    participant.address.clone(),
                    participant.recipient.clone(),
                    coinbase_reward,
                    false,
                )
            };
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
