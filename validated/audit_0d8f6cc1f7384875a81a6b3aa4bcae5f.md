Confirmed: the doc comment above `can_include_tx` explicitly states this is "Used when determining whether a transaction can be added to the mempool, NOT FOR CONSENSUS LOGIC (which might technically allow things that we refuse to add to the mempool)." [1](#0-0) . That confirms the `PoisonMicroblocksDoNotConflict` / same-sequence / same-prev_block / same-version check at lines 6844-6867 is a mempool-admission-only gate, not part of consensus validation, and `handle_poison_microblock`/`process_transaction_payload` never re-checks it during actual block processing.

### Title
Non-conflict-checked `PoisonMicroblock` acceptance in `handle_poison_microblock` allows a miner to hijack the fork-report commission with fabricated, non-equivocating header pairs - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`StacksChainState::handle_poison_microblock` (consensus/Clarity execution path) only verifies that the two supplied microblock headers were signed by the same key via `check_microblock_header_signer`; it never verifies that the two headers actually conflict (same sequence number and same `prev_block`, as `validate_parent_microblock_stream` requires and as the mempool-only `can_include_tx` check enforces). Since this stricter check lives only in the non-consensus mempool-admission path, a miner can include a directly-crafted, non-conflicting `PoisonMicroblock` transaction in their own block and have it accepted network-wide, letting them claim (or "lock in") the reporter slot/sequence for a pubkey hash arbitrarily, ahead of — or instead of — the honest reporter of a real fork.

### Finding Description
The equality that should hold is: **the `reported_seq` recorded via `insert_microblock_poison` equals the true, lowest provable equivocation point for the given microblock pubkey hash** — i.e., `mblock_header_1`/`mblock_header_2` must actually be two distinct, conflicting microblocks (same `sequence`, same `prev_block`, different content) signed by the same key, as constructed internally by `StacksChainState::validate_parent_microblock_stream` (`stackslib/src/chainstate/stacks/db/blocks.rs:3004-3010`, `3030-3036`) and `load_descendant_staging_microblock_stream_with_poison` (`stackslib/src/chainstate/stacks/db/blocks.rs:1387-1412`), both of which only ever produce `TransactionPayload::PoisonMicroblock` pairs that share the same sequence number and same `prev_block`.

That invariant is enforced for mempool-submitted transactions only, in `can_include_tx` (a function explicitly documented as "NOT FOR CONSENSUS LOGIC"): [2](#0-1) . It requires `microblock_header_1.sequence == microblock_header_2.sequence`, `prev_block` equality, `version` equality, and matching recovered pubkeys before a poison tx is even placed in the mempool.

However, the actual consensus-critical execution path — `StacksChainState::handle_poison_microblock`, invoked from `process_transaction_payload` for every `TransactionPayload::PoisonMicroblock` in every block on every node — performs none of these structural checks: [3](#0-2) . It only calls `check_microblock_header_signer`, which recovers each header's pubkey and requires them to be equal: [4](#0-3) . It then unconditionally trusts `mblock_header_1.sequence` as the reported fork point and records it via `insert_microblock_poison` if it's lower than any existing report: [5](#0-4) .

Because a miner assembling their own block does not have to route the `PoisonMicroblock` transaction through the mempool's `can_include_tx` gate at all (that check is purely advisory/mempool-hygiene), the miner can embed a `PoisonMicroblock(header_low_seq, header_high_seq)` pair directly in their own block where `header_low_seq` and `header_high_seq` are two *real, non-conflicting* microblocks from the same honest, non-forked stream (or a fabricated low-seq microblock and a real high-seq one), as long as both recover to the same pubkey hash. Every other node, upon validating/replaying that block, will execute `handle_poison_microblock` with the same lenient logic and accept it as a valid, lower-sequence "report," permanently locking `insert_microblock_poison`'s strictly-less-than override rule against any later, genuinely honest reporter who finds the true fork (which, per the described scenario, occurred at a higher sequence number, e.g. seq 10).

### Impact Explanation
This is a reward-diversion/reward-loss bug bounded to the poison-microblock commission mechanism (fees/coinbase punishment payout), not a chain split, MARF-root divergence, or invalid-block-acceptance issue — all nodes agree on the same (incorrect) `reported_seq` and `reporter`, since `handle_poison_microblock` is deterministic and every node runs the identical (flawed) logic. It matches the **High** severity category: "a poison or reward mis-payment bounded to fees" — the honest reporter of the real seq-10 fork is permanently and deterministically denied the commission because `insert_microblock_poison`'s `<` (strictly-less) override means no later report at seq 10 can ever overwrite an earlier, fabricated seq-3 (or seq-2, etc.) record, per the same-block-race test already present at `stackslib/src/chainstate/stacks/db/transactions.rs:5685-5852` (`process_poison_microblock_multiple_same_block`), which demonstrates that lower-sequence reports always win, with no validation that the reported pair is a genuine fork.

### Likelihood Explanation
The attack requires only a single miner slot (unprivileged, minority stake sufficient — the attacker just needs to win any single tenure to embed their own block) and possession of two previously-broadcast, validly-signed microblocks from any target miner's pubkey (even from a perfectly honest, non-forking stream, or from the two ends of the genuine two-fork situation described). No majority stake, no P2P/RPC privilege, and no coordination beyond normal mining participation is needed. It is repeatable against any active microblock-pubkey-hash record within its `MINER_REWARD_MATURITY` maturation window.

### Recommendation
Move the structural fork-conflict validation (same `sequence`, same `prev_block`, same `version`, differing signature/content, matching pubkeys) out of the mempool-only `can_include_tx`/`PoisonMicroblocksDoNotConflict` check and into `StacksChainState::handle_poison_microblock` (or `check_microblock_header_signer`) itself, so it is enforced as part of consensus for every block, not just mempool admission.

### Proof of Concept
Rust integration test plan (extending the existing `process_poison_microblock_multiple_same_block`/`process_poison_microblock` tests in `stackslib/src/chainstate/stacks/db/transactions.rs`):
1. Register a microblock pubkey hash for miner key `PK` at height `H` via `insert_microblock_pubkey_hash`.
2. Construct two real, non-conflicting, validly-signed microblocks under `PK`: `mblock_seq3` (sequence 3, `prev_block = X`) and `mblock_seq10` (sequence 10, `prev_block = Y`), with `X != Y` and no actual fork between them (a normal linear chain segment).
3. Submit `PoisonMicroblock(mblock_seq3.header, mblock_seq10.header)` directly via `StacksChainState::process_transaction` (bypassing `can_include_tx`/mempool), and assert it succeeds (no `PoisonMicroblocksDoNotConflict`-equivalent error is raised) and that `get_poison_microblock_report(H) == (attacker_addr, 3)`.
4. Separately construct a genuine fork pair at sequence 10 (`mblock_10_a`, `mblock_10_b`, same `prev_block`, same sequence, different content) and submit `PoisonMicroblock(mblock_10_a.header, mblock_10_b.header)` from the honest reporter.
5. Assert the honest reporter's report is rejected/ignored because `mblock_header_1.sequence (10) < seq (3)` is false, i.e., `get_poison_microblock_report(H)` still equals `(attacker_addr, 3)` — proving the exactly-once-correct-reporter guarantee is broken and the attacker permanently wins the commission with a fabricated, non-conflicting pair.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6603-6613)
```rust
    /// Given an outstanding clarity connection, can we append the tx to the chain state?
    /// Used when determining whether a transaction can be added to the mempool, NOT FOR
    /// CONSENSUS LOGIC (which might technically allow things that we refuse to add to
    /// the mempool).
    fn can_include_tx<T: ClarityConnection>(
        clarity_connection: &mut T,
        chainstate_config: &DBConfig,
        has_microblock_pubkey: bool,
        tx: &StacksTransaction,
        tx_size: u64,
    ) -> Result<(), MemPoolRejection> {
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
