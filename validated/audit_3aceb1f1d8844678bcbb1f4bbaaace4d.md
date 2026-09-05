### Title
`handle_poison_microblock` slashes a miner without verifying the two headers actually conflict - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
The consensus-critical `handle_poison_microblock` function only checks that both submitted microblock headers recover to the same public key hash via `check_microblock_header_signer`; it never verifies that the two headers actually represent a genuine fork (same sequence number, same `prev_block`, same version, but differing content/signature). The "do-not-conflict" check that enforces this exists only in the mempool admission path (`will_admit_mempool_tx`), which is not re-run when a block is processed, so a crafted `PoisonMicroblock` transaction with non-conflicting (e.g. duplicate) headers can be included directly in a block and accepted by consensus.

### Finding Description
The claimed invariant is: **slash == a valid, unreported double-signature under the miner's key**, i.e. `mblock_header_1` and `mblock_header_2` must differ in content while both being validly signed by the same microblock key, for the same `sequence`/`prev_block`/`version` (a true equivocation).

`check_microblock_header_signer` only recovers and compares the public key hash of the two headers: [1](#0-0) 

`handle_poison_microblock` calls only this check before recording the poison report and producing the slash result: [2](#0-1) [3](#0-2) 

If `mblock_header_1` and `mblock_header_2` are identical (same sequence, same `prev_block`, same signature — i.e. the *same* microblock header submitted twice), `check_microblock_header_signer` trivially succeeds because both recovered public keys are equal to themselves. No code path in `handle_poison_microblock` checks `mblock_header_1 != mblock_header_2`, nor that `sequence`/`prev_block`/`version` match between the two headers, nor that the headers are genuinely divergent (double-signed) rather than a duplicate of the same, single, legitimately-produced microblock.

The only place these consistency checks exist is in the *mempool admission* logic, which is not part of consensus (a miner can skip mempool entirely and place a self-crafted transaction directly into their own block): [4](#0-3) 

Since `process_transaction` invokes `run_poison_microblock` → `handle_poison_microblock` directly during block processing (not through the mempool admission gate), any block that carries a `PoisonMicroblock(header, header)` (duplicate header) transaction, signed by any unprivileged account and referencing any microblock public key hash the target miner has legitimately published, is fully accepted by every honest node. The result is used downstream to slash/redirect that miner's coinbase to the "reporter," despite there being no actual double-signature — breaking the stated equality (slash triggered without equivocation).

### Impact Explanation
This allows a "poison or reward mis-payment" bounded to a single miner's coinbase: an unprivileged attacker (any account, not needing majority stake, not needing to be a miner) can cause a legitimately-produced miner's future/matured coinbase reward to be slashed and commissioned to the attacker's chosen reporter address, even though that miner never double-signed a microblock fork. This matches the "High — a poison or reward mis-payment bounded to fees" / potentially "block-reward theft" category since the reporter receives commission taken from an honest miner's coinbase reward.

### Likelihood Explanation
- The attacker needs only: (a) knowledge of any microblock header the target miner has published (public information, broadcast on the network) and (b) the ability to get a `PoisonMicroblock(header, header)` transaction included in *any* block — either by being a miner themselves (a single miner slot, permitted under the threat model) or by having any miner (including a colluding minority miner) include their transaction.
- No majority stake or signer collusion is required.
- The bug is deterministic and repeatable against any published microblock header, as long as it is within `MINER_REWARD_MATURITY` of the current height.

### Recommendation
In `handle_poison_microblock` (or `check_microblock_header_signer`), before accepting the report, additionally require:
- `mblock_header_1.sequence == mblock_header_2.sequence`
- `mblock_header_1.prev_block == mblock_header_2.prev_block`
- `mblock_header_1.version == mblock_header_2.version`
- `mblock_header_1 != mblock_header_2` (they must differ, e.g. in `tx_merkle_root` or signature) so the pair represents a genuine equivocation rather than a duplicate submission of the same header.

These are exactly the checks already present in `will_admit_mempool_tx` (`stackslib/src/chainstate/stacks/db/blocks.rs:6844-6867`); they must be duplicated into the consensus-critical execution path (`transactions.rs::handle_poison_microblock` / `check_microblock_header_signer`) since mempool admission is not re-validated during block processing.

### Proof of Concept
Rust integration test plan (chainstate-level, analogous to existing tests in `stackslib/src/chainstate/stacks/db/transactions.rs` around lines 5509-5597):
1. Insert a microblock public key hash for a miner (`StacksChainState::insert_microblock_pubkey_hash`).
2. Construct a single legitimately-signed `StacksMicroblockHeader` (`mblock_1`) with a given `sequence`/`prev_block`.
3. Set `mblock_header_2 = mblock_header_1.clone()` (i.e., an exact duplicate, NOT an independently-produced conflicting header).
4. Build and sign a `PoisonMicroblock(mblock_header_1, mblock_header_2)` transaction from an arbitrary reporter account.
5. Call `StacksChainState::process_transaction` directly (bypassing `will_admit_mempool_tx`).
6. **Assertion (equality broken)**: assert the call returns `Ok(..)` and `StacksChainState::get_poison_microblock_report` returns `Some((reporter_addr, sequence))` — i.e., a slash/report was recorded — even though `mblock_header_1 == mblock_header_2` (no double-signature/fork exists). Compare against the correct behavior, which should return `Err(Error::InvalidStacksTransaction(..))` because the headers do not conflict.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L686-712)
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

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6844-6867)
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
```
