### Title
Missing header-distinctness check in `check_microblock_header_signer` allows reward theft via non-equivocation "poison" report - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::check_microblock_header_signer` (stackslib/src/chainstate/stacks/db/transactions.rs) only verifies that the two supplied `StacksMicroblockHeader` values recover to the *same* public-key hash; it never checks that `mblock_header_1` and `mblock_header_2` are semantically distinct microblocks (i.e., differ in signable content such as `block_hash`, `tx_merkle_root`, `sequence`, or `prev_block`). `handle_poison_microblock` then unconditionally trusts this pair as proof of equivocation and pays a coinbase commission to the reporter.

### Finding Description
The invariant the protocol needs is: *reward paid to a `PoisonMicroblock` reporter == proof that the pubkeyhash owner produced two genuinely different microblocks at the same stream position*. The code as written only enforces: [1](#0-0) 

i.e. it recovers a pubkey from each header independently and compares only `pkh1 != pkh2`. There is no assertion that `mblock_header_1.sequence == mblock_header_2.sequence`, that `mblock_header_1.prev_block == mblock_header_2.prev_block`, or that the two headers' signable content (`block_hash`/`tx_merkle_root`) actually differs.

`handle_poison_microblock` then consumes `mblock_header_1.sequence` directly to record the report and pay the reward, again without cross-checking it against `mblock_header_2.sequence` or verifying the headers are not the same microblock re-encoded: [2](#0-1) [3](#0-2) 

Because ECDSA signatures are malleable (given a valid `(r, s)` there is a second valid `(r, n-s)` with an adjusted recovery id that recovers to the *same* public key for the *same* message), an attacker who observes one honestly-signed microblock header can derive a second, byte-different `StacksMicroblockHeader` whose non-signature fields (`sequence`, `prev_block`, `tx_merkle_root`, etc.) are identical to the original, differing only in the signature encoding. `check_microblock_header_signer` will still recover `pkh1 == pkh2` for this pair because both signatures recover the honest miner's key, and no other check rejects the pair for being the same message. `handle_poison_microblock` then treats this as a legitimate double-sign and issues a poison report/commission entitlement against the honest miner, even though the miner only ever signed one microblock.

### Impact Explanation
This allows an unprivileged transaction broadcaster to fabricate a "poison" report against a miner who never equivocated, causing that miner's coinbase to be diverted/slashed to the attacker's commission at block maturation (`find_mature_miner_rewards`). This is a reward-theft primitive directed at an honest miner's coinbase, matching the "block-reward theft" Critical category, and is repeatable against any miner whose microblock signature the attacker can observe (which is trivial, since microblocks are broadcast).

### Likelihood Explanation
Preconditions are modest: the attacker needs to observe one broadcast, valid, signed microblock header from a miner (public information) and be able to derive a malleated `(r, n-s)` signature with the matching recovery id — a purely local cryptographic computation requiring no stake, no BTC spend, and no privileged role. The only open question, which could not be fully verified from the available code, is whether the underlying secp256k1 recoverable-signature verification path used by `check_recover_pubkey` enforces canonical low-S signatures (which would block basic malleability). This detail lives outside the reviewed file (`stacks_common`/`secp256k1` signing utilities) and was not confirmed by the exploration performed.

### Recommendation
In `check_microblock_header_signer` (or immediately before calling it in `handle_poison_microblock`), additionally require: `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, and that the two headers' signable content (excluding the signature field) differs — otherwise reject with `InvalidStacksTransaction`. This ensures the pair actually proves two distinct signed microblocks at the same stream position rather than a single message re-signed via malleability.

### Proof of Concept
1. Construct a chainstate with one miner key and one genuinely broadcast, valid `StacksMicroblockHeader` (`header_1`).
2. Programmatically derive `header_2` by copying all non-signature fields from `header_1` and replacing the signature with its malleated counterpart `(r, n-s)` plus adjusted recovery id, verifying it still recovers the same pubkey.
3. Submit a `TransactionPayload::PoisonMicroblock(header_1, header_2)` transaction through `process_transaction_payload` → `handle_poison_microblock`.
4. Assert that `handle_poison_microblock` returns `Err(Error::InvalidStacksTransaction(..))` (expected fix behavior) rather than `Ok(Value::Tuple(..))` crediting a report/commission — under the current code, this call is expected to incorrectly succeed, confirming the missing distinctness check.

*Note: full verification of this finding was limited by not being able to inspect the beginning of `check_microblock_header_signer` (only the tail comparing `pkh1 != pkh2` was directly observed) and by uncertainty over whether the crate used for ECDSA recovery already rejects non-canonical (high-S) signatures, which would mitigate the malleability precondition.*

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L700-713)
```rust
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
