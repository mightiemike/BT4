### Title
`handle_poison_microblock` slashes honest miners on duplicate (non-conflicting) microblock headers - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
Neither `check_microblock_header_signer` nor the surrounding `handle_poison_microblock` logic, nor the mempool-level `PoisonMicroblocksDoNotConflict` guard, ever verifies that `mblock_header_1.block_hash() != mblock_header_2.block_hash()`. An attacker can submit `PoisonMicroblock(h, h.clone())` for a single, honestly-produced microblock header `h` and it will pass every existing check, causing the honest miner's key to be falsely recorded as having equivocated at `h.sequence`, entitling the attacker/reporter to a commission of that miner's coinbase.

### Finding Description
The claimed equality is: **valid poison-microblock slashing == two DISTINCT `StacksMicroblockHeader`s (differing in `block_hash()`) at the identical sequence, both independently verifying under the miner's key.**

Tracing the code:
- `check_microblock_header_signer` at [1](#0-0)  only recovers the pubkey hash from each header and compares `pkh1 != pkh2`. If `mblock_header_1` and `mblock_header_2` are literally the same header (same `sequence`, `prev_block`, `tx_merkle_root`, `signature`), `pkh1 == pkh2` trivially, and the function returns `Ok(pkh1)` — it never checks `mblock_header_1.block_hash() != mblock_header_2.block_hash()`.
- `handle_poison_microblock` at [2](#0-1)  calls only this signer check before proceeding to look up `get_microblock_pubkey_hash_height(&pubkh)` and validate the maturity window at [3](#0-2) , then unconditionally inserts a poison report crediting the sender with a fork "discovered" at `mblock_header_1.sequence` at [4](#0-3) , explicitly noting the reporter becomes "entitled to a commission of the punished miner's coinbase."
- The only other existing guard, the mempool-level `PoisonMicroblocksDoNotConflict` check in `blocks.rs`, at [5](#0-4) , checks that `sequence`, `prev_block`, and `version` match between the two headers and that their recovered pubkeys match — all of which trivially pass when the two headers are byte-identical. This check exists to catch mismatched/garbage pairs, not to catch duplicate submission of the same header.
- All test coverage in the repo (`process_poison_microblock`, `process_poison_microblock_multiple_same_block`, etc., e.g. [6](#0-5) ) explicitly asserts `mblock_1 != mblock_2` before building the transaction, confirming the codebase's implicit assumption of distinctness is never enforced in production code — only exercised in test fixtures.

Exploit flow: an unprivileged attacker observes any legitimately broadcast `StacksMicroblockHeader` `h` signed by a miner's microblock key (this is public network data). They construct `TransactionPayload::PoisonMicroblock(h.clone(), h.clone())`, sign it with their own key as `sender_principal`, and submit it (it passes the mempool's `PoisonMicroblocksDoNotConflict` check since all fields are equal to themselves, and it passes `handle_poison_microblock`'s only real gate, `check_microblock_header_signer`, since `pkh1 == pkh2` trivially). The transaction is accepted into a block and processed, and the honest miner is falsely marked as poisoned at `h.sequence`, with the attacker recorded as `reporter_principal` entitled to that miner's coinbase commission.

### Impact Explanation
This causes **block-reward theft** matching the Critical category: an honest miner who never equivocated has their coinbase reward diverted (in whole or part, per commission logic) to an attacker who fabricated a "poison" report from a single honest header duplicated. This is not bounded to fees — it is a wrongful redirection of the miner's block reward, and it is repeatable against any miner whose microblock public key hash is currently within the `MINER_REWARD_MATURITY` window, for every microblock they've ever produced.

### Likelihood Explanation
The only precondition is that the target miner has produced at least one microblock (their pubkey hash registered via `insert_microblock_pubkey_hash`) within the maturity window, and the attacker can observe that header (trivially — it is broadcast on the P2P network). No majority stake, no signer key, no admin access is required — an unprivileged network participant can submit a standard token-transfer-fee transaction with this payload. This is fully repeatable against any active miner every time they produce a microblock.

### Recommendation
Add an explicit distinctness check in `check_microblock_header_signer` (or immediately before calling it in `handle_poison_microblock`) requiring `mblock_header_1.block_hash() != mblock_header_2.block_hash()`, returning `Error::InvalidStacksTransaction` otherwise. The same check should be added to the mempool's `PoisonMicroblocksDoNotConflict` validation in `blocks.rs` to reject early.

### Proof of Concept
Rust integration test plan (mirroring existing `process_poison_microblock` test structure in `stackslib/src/chainstate/stacks/db/transactions.rs`):
1. Register an honest miner's microblock pubkey hash via `StacksChainState::insert_microblock_pubkey_hash`.
2. Construct a single honestly-signed `StacksMicroblockHeader` `h` (one `make_signed_microblock` call, not two).
3. Build `TransactionPayload::PoisonMicroblock(h.clone(), h.clone())`, sign with an unrelated reporter key, and call `StacksChainState::process_transaction`.
4. Assert on both sides of the equality:
   - Left (claimed-safe) side: assert `h.block_hash() == h.block_hash()` (trivially true, showing no real fork exists).
   - Right (current-behavior) side: assert whether `process_transaction` returns `Ok(..)` with a poison report inserted (current buggy behavior) vs. the expected `Err(Error::ClarityError(ClarityError::BadTransaction(_)))` / `InvalidStacksTransaction` (fixed behavior).
5. Confirm via `StacksChainState::get_poison_microblock_report` that a report was created for a miner who submitted only one distinct header, proving false slashing occurred prior to the fix.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-757)
```rust
    pub fn handle_poison_microblock(
        env: &mut ExecutionState,
        invoke_ctx: &InvocationContext,
        mblock_header_1: &StacksMicroblockHeader,
        mblock_header_2: &StacksMicroblockHeader,
    ) -> Result<Value, Error> {
        let cost_before = env.global_context.cost_track.get_total();

        // encodes MARF reads for loading microblock height and current height, and loading and storing a
        // poison-microblock report
        runtime_cost(ClarityCostFunction::PoisonMicroblock, env, 0)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        let sender_principal = match &invoke_ctx.sender {
            Some(ref sender) => {
                if let PrincipalData::Standard(sender) = sender.clone() {
                    sender
                } else {
                    panic!(
                        "BUG: tried to handle poison microblock without a standard principal sender"
                    );
                }
            }
            None => {
                panic!("BUG: tried to handle poison microblock without a sender");
            }
        };

        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L768-803)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5525-5529)
```rust
            let mblock_1 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
            let mblock_2 =
                make_signed_microblock(&block_privk, &privk, BlockHeaderHash([0x11; 32]), 123);
            assert!(mblock_1 != mblock_2);
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
