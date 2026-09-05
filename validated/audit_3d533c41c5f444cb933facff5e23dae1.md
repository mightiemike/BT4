### Title
Missing sequence/prev_block equality check in `check_microblock_header_signer` allows slashing an honest miner without a genuine microblock equivocation - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`check_microblock_header_signer` (transactions.rs:686-713) only verifies that both microblock headers recover to the same public-key hash (`pkh1 == pkh2`); it never checks that `mblock_header_1.sequence == mblock_header_2.sequence` or that `mblock_header_1.prev_block == mblock_header_2.prev_block`. `handle_poison_microblock` (transactions.rs:722-857) relies solely on this function to accept the pair as valid "poison" (equivocation) evidence, then records a slash/report against the miner's pubkey hash and pays a commission to the reporter.

### Finding Description
The equality that should be enforced for genuine poison-microblock evidence is: same `sequence` AND same `prev_block` (i.e., a genuine fork/double-sign at one point in the microblock stream), with differing content (e.g., `tx_merkle_root`). Instead, the code only checks: [1](#0-0) 
which recovers `pkh1` and `pkh2` via `check_recover_pubkey()` and rejects only if `pkh1 != pkh2`. There is no comparison of `sequence` or `prev_block` between the two headers anywhere in this function or in the caller.

`handle_poison_microblock` then takes `pubkh` from this check, looks up `get_microblock_pubkey_hash_height(&pubkh)`, verifies maturity window, and if valid, calls `insert_microblock_poison`/records a report keyed off `mblock_header_1.sequence`: [2](#0-1) [3](#0-2) 

Because the same private key legitimately signs every microblock a miner produces in a tenure (each with increasing `sequence` and chained `prev_block`), an attacker can take any two microblocks the miner honestly published — which do NOT conflict at all, e.g. sequence 3 with prev_block X, and sequence 7 with prev_block Y — and submit them as `TransactionPayload::PoisonMicroblock(mblock_header_1, mblock_header_2)`. Since both recover to the same `pkh`, `check_microblock_header_signer` returns `Ok(pkh)`, and the miner is slashed for a fabricated equivocation that never occurred.

### Impact Explanation
This results in block-reward theft/loss: an honest miner who never double-signed is slashed via `insert_microblock_poison`, and the attacker (as `sender_principal`/reporter) becomes entitled to a commission of the punished miner's coinbase. This is a reward mis-payment directly attributable to a validation gap in this repository, reachable by any unprivileged party who can observe two microblocks from a target miner and submit a transaction.

### Likelihood Explanation
Preconditions are minimal and commonly satisfied: any miner that has produced at least two microblocks in the maturity window (`MINER_REWARD_MATURITY`) is a valid target, since any two of their broadcast/network-visible microblocks (regardless of sequence/prev_block) can be paired. The attacker needs no special stake, no majority position, and no signer/miner privileges — only the ability to submit an ordinary Stacks transaction. This is trivially repeatable against every actively-mining honest miner.

### Recommendation
In `check_microblock_header_signer` (or immediately in `handle_poison_microblock` before using the result), require `mblock_hdr_1.sequence == mblock_hdr_2.sequence`, `mblock_hdr_1.prev_block == mblock_hdr_2.prev_block`, and that the headers are not identical (differ in some other field, e.g. `tx_merkle_root`), in addition to the existing pubkey-hash equality check. Reject the transaction as invalid if these conditions do not hold.

### Proof of Concept
```rust
// Pseudocode integration test plan
// 1. Generate a StacksPrivateKey `miner_key` and derive pubkey hash `pubkh`.
// 2. Simulate legitimate mining: construct StacksMicroblockHeader h1 with
//    sequence = 3, prev_block = block_hash_A, signed with miner_key.
// 3. Construct StacksMicroblockHeader h2 with
//    sequence = 7, prev_block = block_hash_B (unrelated, non-conflicting),
//    signed with miner_key.
// 4. Register pubkh via insert_microblock_pubkey_hash at some block height H
//    (simulating that the miner legitimately announced pubkh).
// 5. Build TransactionPayload::PoisonMicroblock(h1, h2), wrap in a signed
//    StacksTransaction from an arbitrary "attacker" account.
// 6. Call StacksChainState::process_transaction (or directly
//    handle_poison_microblock) with this payload.
// 7. Assert (equality check BEFORE fix): call returns Ok(..) and
//    get_microblock_poison_report(H) / get_poison_microblock_report returns
//    Some((attacker_principal, 3)) -- i.e. the miner is slashed -- even though
//    h1.sequence != h2.sequence and h1.prev_block != h2.prev_block (no genuine
//    equivocation).
// 8. Assert (equality check AFTER fix): the same call returns
//    Err(Error::InvalidStacksTransaction(..)) because sequence/prev_block
//    mismatch is now detected, and no poison report is recorded.
``` [4](#0-3) [2](#0-1)

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-757)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
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
