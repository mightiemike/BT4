### Title
`handle_poison_microblock` slashes miners for non-conflicting microblock headers because it only checks signer equality, not sequence/prev_block equality - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`StacksChainState::handle_poison_microblock` (called from `process_transaction`) validates a `PoisonMicroblock` payload solely via `check_microblock_header_signer`, which only asserts `pkh1 == pkh2` [1](#0-0) . It never checks that `mblock_header_1.sequence == mblock_header_2.sequence`, that the two headers share the same `prev_block`, or that they are otherwise distinct-but-colliding (the actual definition of equivocation). That conflict check exists only in the mempool admission path, `will_admit_mempool_tx`, not in the consensus-critical transaction-processing path.

### Finding Description
The claimed equality is: **reward slashed == exactly one valid double-signature at the same sequence (equivocation)**. Tracing the code shows this equality is not enforced in the transaction-processing path that actually applies the poison and grants a commission.

- `handle_poison_microblock` calls `check_microblock_header_signer(mblock_header_1, mblock_header_2)`, whose entire logic is: recover `pkh1` from header 1, recover `pkh2` from header 2, and return an error only if `pkh1 != pkh2` [2](#0-1) .
- After that single check, `handle_poison_microblock` immediately proceeds to look up the pubkey-hash height, check maturity, and record/credit a poison report keyed by `mblock_header_1.sequence` — with no additional validation of `mblock_header_2`, sequence equality, or hash divergence [3](#0-2) .
- Because both `mblock_header_1` (sequence N) and `mblock_header_2` (sequence N+1) from a legitimate, non-forked stream are signed by the same miner microblock key, `pkh1 == pkh2` trivially holds even though there is no equivocation at any shared sequence — the miner simply extended their own stream honestly.
- The genuine "did these two headers collide" check — `prior_microblock.header.sequence == cur_microblock.header.sequence && prior_microblock.block_hash() != cur_microblock.block_hash()` — exists only in `validate_parent_microblock_stream` in `blocks.rs`, which is used to *construct* legitimate poison payloads during block-stream validation, not to *validate* an arbitrary submitted `PoisonMicroblock` transaction [4](#0-3) .
- The only place that checks `sequence`/`prev_block`/`version` equality between the two headers of a submitted `PoisonMicroblock` transaction is `will_admit_mempool_tx`, which is a mempool-relay policy gate, not part of consensus-critical block/transaction application: `if microblock_header_1.sequence != microblock_header_2.sequence || microblock_header_1.prev_block != microblock_header_2.prev_block || microblock_header_1.version != microblock_header_2.version { return Err(MemPoolRejection::PoisonMicroblocksDoNotConflict); }` [5](#0-4) .

Exploit flow: an attacker (who can be the microblock reporter, and only needs to be able to get a `PoisonMicroblock` transaction into a mined block — e.g., by running their own miner slot, which is within the unprivileged attacker capability set) crafts a `PoisonMicroblock(header_N, header_{N+1})` using two headers legitimately produced and signed by an honest miner's microblock stream. This transaction bypasses `will_admit_mempool_tx`'s conflict check entirely if injected directly by a block producer rather than relayed through the mempool. When `process_transaction` executes it via `handle_poison_microblock`, the sole check `pkh1 == pkh2` passes (since both really were signed by the honest miner), and the honest miner's key gets flagged as "slashed," crediting the reporter's principal with entitlement to the miner's future coinbase commission — despite no equivocation having occurred.

### Impact Explanation
This results in block-reward theft: an honest, non-equivocating miner's future coinbase can be redirected via the "poison" commission mechanism to an attacker-controlled reporter address, purely because two legitimately sequential (non-conflicting) headers were submitted. Since `process_transaction`/`handle_poison_microblock` is deterministic and part of core state transition, all honest nodes would apply this "poison" identically (no chain split), but the reward-payment invariant ("only actual double-signers get slashed") is broken. This matches the Critical category "block-reward theft/double-payment/loss."

### Likelihood Explanation
The attacker needs only: (1) the ability to observe/record two genuine consecutive microblock headers from any miner's real (non-forked) microblock stream — trivially obtainable by any network participant relaying/observing microblocks, and (2) the ability to get a `PoisonMicroblock(header_N, header_{N+1})` transaction included in a mined block, bypassing the mempool-only `PoisonMicroblocksDoNotConflict` check. Since the attacker is permitted a single miner slot, they can include the crafted transaction directly in their own tenure's block, entirely sidestepping `will_admit_mempool_tx`. No majority stake, no signer key, and no admin access are required — this is fully within the unprivileged attacker's described capabilities and is repeatable against every miner whose microblock stream is observed.

### Recommendation
Move the conflict-defining checks currently only present in `will_admit_mempool_tx` (`sequence` equality, `prev_block` equality/consistency, and header-hash inequality) into `check_microblock_header_signer` or directly into `handle_poison_microblock`, so that consensus-critical transaction processing rejects any `PoisonMicroblock` payload whose two headers do not represent a genuine equivocation at the same sequence number.

### Proof of Concept
Rust integration test plan (extending the existing `process_poison_microblock_valid` test pattern in `stackslib/src/chainstate/stacks/db/transactions.rs`):
1. Build a legitimate, non-forked two-microblock stream signed by a single miner key: `mblock_1` at `sequence = N`, `prev_block = genesis_block_hash`; `mblock_2` at `sequence = N+1`, `prev_block = mblock_1.block_hash()` (i.e., a valid contiguous chain, not a fork).
2. Construct `TransactionPayload::PoisonMicroblock(mblock_1.header.clone(), mblock_2.header.clone())`, sign with an unrelated reporter key, and call `StacksChainState::process_transaction` directly (bypassing `will_admit_mempool_tx`).
3. **Assert the equality on both sides:** before processing, assert `mblock_1.header.sequence != mblock_2.header.sequence` (no shared-sequence collision exists — i.e., no equivocation). After processing, assert that `process_transaction` returns an `Err` (rejects the transaction) rather than `Ok` with a poison report recorded via `get_poison_microblock_report`, and that no commission/reporter credit is created.
4. Current code will show `process_transaction` succeeds and credits the reporter — demonstrating the break: the reward-payment equality "credited == genuine double-sign at the same sequence" fails.

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

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3021-3037)
```rust
        for (j, cur_microblock) in signed_microblocks.iter().skip(1).enumerate() {
            if prior_microblock.header.sequence == cur_microblock.header.sequence
                && prior_microblock.block_hash() != cur_microblock.block_hash()
            {
                // deliberate microblock fork
                debug!(
                    "Deliberate microblock fork at sequence {}",
                    prior_microblock.header.sequence
                );
                return Some((
                    j, // j := `index in signed_microblocks of cur_microblock - 1`
                    Some(TransactionPayload::PoisonMicroblock(
                        prior_microblock.header.clone(),
                        cur_microblock.header.clone(),
                    )),
                ));
            }
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
