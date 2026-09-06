### Title
Poison-microblock reward theft via identical (non-equivocating) header submitted twice - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` only verifies that two supplied `StacksMicroblockHeader` objects were signed by the same key; it never checks that they are distinct headers (different hash/content) proving an actual fork/equivocation. `handle_poison_microblock` calls this check and, on success, unconditionally slashes the miner identified by the recovered public-key hash and records the reporter for a coinbase commission, even when `mblock_header_1` and `mblock_header_2` are the same object.

### Finding Description
The broken equality is: "poison-microblock reward paid == exactly one valid, previously-unreported DOUBLE-signature (two conflicting headers) by the slashed miner." The code path is: [1](#0-0) 

`check_microblock_header_signer` recovers `pkh1` and `pkh2` from the two headers and only rejects if `pkh1 != pkh2`. It performs no comparison of the headers' hashes, sequence numbers, or any other field that would distinguish two competing signed microblocks from one single header submitted twice. An attacker who takes a single honestly-broadcast, validly-signed microblock header and submits it as both `mblock_header_1` and `mblock_header_2` in a `PoisonMicroblock` payload will trivially pass this check because `pkh1 == pkh2` (they are literally computed from the same bytes).

`handle_poison_microblock` then proceeds: [2](#0-1) 

It looks up the height at which `pubkh` was recorded via `get_microblock_pubkey_hash_height`, verifies the maturity window, and — finding no prior report — records the poison report and commission entitlement: [3](#0-2) 

No other guard in the payload path (searched `PoisonMicroblock` handling in `stackslib/src/chainstate/stacks/db/transactions.rs`) checks that `mblock_header_1` and `mblock_header_2` differ. There is no equivalent of "assert `mblock_header_1.block_hash() != mblock_header_2.block_hash()`" or a comparison of the two headers' content anywhere in this call chain. The maturation window and pubkey-height lookup only gate on *when* the key was used, not on *whether two conflicting signatures actually exist*.

### Impact Explanation
This is block-reward theft: an honest, non-equivocating miner's coinbase is diverted at maturation to an attacker who never observed or fabricated any conflicting signature — they only replayed a single legitimately broadcast header. This matches the Critical category "block-reward theft/double-payment/loss." The loss is deterministic and falls on the honest miner's coinbase reward; the attacker's principal is recorded as `reporter_principal` and becomes entitled to the commission via the returned tuple, which downstream (`find_mature_miner_rewards`/`calculate_miner_reward`, out of the immediate scope of this file but consuming the poison record) redirects reward funds.

### Likelihood Explanation
The attack requires only observing one broadcast microblock header from a normal miner's stream over P2P (no privileged access), constructing a `PoisonMicroblock` transaction with that header cloned into both slots, and submitting it before `MINER_REWARD_MATURITY` blocks have elapsed since the key was recorded. This is fully within reach of an unprivileged, minority-stake participant and is repeatable against any miner whose microblock headers become publicly visible before maturity — which is the normal, expected case for every miner using microblocks.

### Recommendation
In `check_microblock_header_signer` (or immediately before it in `handle_poison_microblock`), require that the two headers are not identical — e.g., reject if `mblock_hdr_1.block_hash() == mblock_hdr_2.block_hash()` or if all fields (including `sequence` and `signature`) match — so that only genuine equivocation (two different headers signed by the same key, e.g. differing in `sequence`, `prev_block`, or `tx_merkle_root`, but with the same key) is accepted as valid proof of a fork.

### Proof of Concept
1. Build a Rust integration test using existing chainstate/microblock test scaffolding (similar to `stackslib/src/chainstate/stacks/tests/block_construction.rs`'s poison-microblock tests).
2. Have an honest miner produce and broadcast one microblock stream; record its `pubkh` via `insert_microblock_pubkey_hash` at height `H` (as normally occurs during block processing).
3. Extract a single signed `StacksMicroblockHeader` `hdr` from that stream.
4. Construct `TransactionPayload::PoisonMicroblock(hdr.clone(), hdr.clone())`, submit and mine it in a block at height `H' <= H + MINER_REWARD_MATURITY`.
5. Assert: `check_microblock_header_signer(&hdr, &hdr)` returns `Ok(pkh)` (equality side 1: passes despite no equivocation).
6. Assert `env.global_context.database.get_microblock_poison_report(H)` returns `Some((attacker_principal, hdr.sequence))` after processing.
7. Advance the chain past `MINER_REWARD_MATURITY` and assert via `find_mature_miner_rewards`/`calculate_miner_reward` that the honest miner's `MinerReward` for height `H` is zero/redirected, while the attacker (who supplied no genuine double-signature) receives the commission — demonstrating the equality "reward paid == genuine double-signature" is violated.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-803)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L842-856)
```rust
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
