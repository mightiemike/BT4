### Title
`handle_poison_microblock` slashes a miner without proof of an actual microblock fork because `check_microblock_header_signer` never verifies the two headers diverge - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`check_microblock_header_signer` (called from `handle_poison_microblock`) only confirms that `mblock_header_1` and `mblock_header_2` recover to the same public-key hash; it never checks that the two headers actually conflict (same `sequence`/`prev_block` but a divergent `tx_merkle_root`, or even that they are two distinct objects at all). An attacker can take a single, legitimately broadcast microblock header and submit it as *both* `mblock_header_1` and `mblock_header_2` in a `PoisonMicroblock` payload, and the transaction will still be accepted and will slash the honest miner's coinbase reward.

### Finding Description
The claimed equality is: `poison_microblock_commission(coinbase)` should be paid out `iff` two DIFFERENT, conflicting microblocks (same sequence/prev_block, divergent tx_merkle_root) were signed by the same miner key. In the actual code, the equality that is enforced is only `check_recover_pubkey(mblock_header_1) == check_recover_pubkey(mblock_header_2)`: [1](#0-0) 

`handle_poison_microblock` calls this function and, once the pubkey-hash check passes and the height/maturity window matches, unconditionally records a poison report and commission entitlement, using only `mblock_header_1.sequence` (no comparison of header content at all): [2](#0-1) 

The only place in the codebase that checks that the two headers share `sequence`/`prev_block`/`version` is the *mempool admission* path (`will_admit_mempool_tx`), which is not part of consensus-critical transaction processing and, critically, still does not require the `tx_merkle_root`/signature to differ, nor that the two headers be distinct objects: [3](#0-2) 

Because a block producer is not required to route transactions through the mempool at all (they can construct blocks directly), the consensus-level guard is entirely the two-line `pkh1 != pkh2` check in `check_microblock_header_signer`. An attacker's exact input: obtain any single real, previously-broadcast, signed microblock header `H` from any miner whose pubkey hash was already recorded via `insert_microblock_pubkey_hash`/`get_microblock_pubkey_hash_height` (this happens for every anchored block that produced at least one microblock), and construct `PoisonMicroblock(H, H)` (byte-identical headers). `check_recover_pubkey(H) == check_recover_pubkey(H)` trivially holds, `get_microblock_pubkey_hash_height` returns a real height within `MINER_REWARD_MATURITY`, and `insert_microblock_poison` fires — despite there being no second, conflicting microblock and no actual equivocation by the miner.

Existing guards do not stop this: `check_tenure_tx`, `validate_parent_microblock_stream`, and the maturation window check operate over height/maturity and stream contiguity, not over whether the two poison headers are actually distinct/conflicting content. `validate_parent_microblock_stream` does independently detect *real* duplicate-sequence forks and can synthesize its own poison payload, but that does not prevent a third party from separately crafting and submitting a fabricated `PoisonMicroblock(H, H)` transaction for any miner whose pubkey hash is on record.

### Impact Explanation
This allows an unprivileged attacker to falsely slash any miner's coinbase reward and claim the reporter's commission without that miner ever having produced two conflicting microblocks — this is a direct, network-wide, consensus-state reward-theft / mis-payment: the miner's legitimate reward is replaced by an unearned commission payout to an attacker, and this state (the poison report and payment) is part of consensus and will be agreed upon identically by all honest nodes (since all nodes run the same flawed check), producing consistent-but-wrong ledger state rather than a fork. This matches "block-reward theft/double-payment/loss" (Critical) since it is repeatable against every miner that has ever signed at least one microblock.

### Likelihood Explanation
Preconditions are trivially met: any miner that has produced at least one microblock has its pubkey hash recorded via `get_microblock_pubkey_hash_height`, and the current block height merely needs to be within `MINER_REWARD_MATURITY` of that recording (a normal, wide window). The attacker needs no signing keys, no majority stake, and no privileged role — only the ability to submit an ordinary standard transaction (`PoisonMicroblock`) referencing a publicly broadcast microblock header twice. This is fully repeatable against every miner in the network history within the maturity window.

### Recommendation
In `check_microblock_header_signer` (or in `handle_poison_microblock` before calling it), explicitly require that the two headers represent a genuine equivocation: assert `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, `mblock_header_1.version == mblock_header_2.version`, AND that they diverge in content (`mblock_header_1.tx_merkle_root != mblock_header_2.tx_merkle_root` or, at minimum, that the two headers/signatures are not identical). Reject the transaction with an error (mirroring `MemPoolRejection::PoisonMicroblocksDoNotConflict`) if the headers are identical or don't represent a real fork, and enforce this at the consensus/transaction-processing layer, not merely in the mempool admission path.

### Proof of Concept
Rust integration test in `stackslib/src/chainstate/stacks/db/transactions.rs` test module:
1. Set up a chainstate; call `insert_microblock_pubkey_hash(&mut conn, height, &block_pubkh)` for a known `block_privk`.
2. Construct one legitimately signed `StacksMicroblockHeader` `mblock` (using `make_signed_microblock`), with a real signature from `block_privk`.
3. Build `TransactionPayload::PoisonMicroblock(mblock.header.clone(), mblock.header.clone())` (identical headers - `assert_eq!(mblock.header, mblock.header)`), sign as a reporter, and call `StacksChainState::process_transaction`.
4. Assert (broken-equality check): `StacksChainState::get_poison_microblock_report(&mut conn, height)` returns `Some((reporter_addr, mblock.header.sequence))`, i.e., the transaction succeeds and slashes/reports the miner — even though `mblock_header_1 == mblock_header_2` (no fork exists).
5. Contrast with expected correct behavior: the same call should instead return `Err(...)` (e.g., a `PoisonMicroblocksDoNotConflict`-style error) since no second, conflicting microblock was presented.

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
