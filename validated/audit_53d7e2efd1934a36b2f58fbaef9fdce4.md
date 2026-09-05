### Title
Poison-microblock reward slashing accepted with byte-identical headers (no equivocation proof required) - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`handle_poison_microblock` accepts any `PoisonMicroblock` payload as long as `check_microblock_header_signer` recovers the same public-key hash from both header slots, and that function trivially returns `Ok` when `mblock_hdr_1 == mblock_hdr_2` byte-for-byte. Since there is no check anywhere in this code path that the two headers are actually distinct (i.e., proof of a real fork), an attacker can duplicate a single, honestly-signed, previously broadcast microblock header into both slots and get the signing miner slashed even though no equivocation ever occurred.

### Finding Description
The equality that matters here is `mblock_header_1 != mblock_header_2` (distinctness, i.e. proof that the miner signed two *different* messages with the same key at the same position — actual equivocation). The code never asserts this.

`check_microblock_header_signer` [1](#0-0)  only recovers the pubkey hash from each header independently and compares `pkh1 != pkh2`. If the two input headers are byte-identical, `pkh1` and `pkh2` are computed from the same bytes and are trivially equal, so the function returns `Ok(pkh1)` with zero proof of a fork.

`handle_poison_microblock` calls this check and then proceeds directly to look up `get_microblock_pubkey_hash_height(&pubkh)`, verify maturity, and record/overwrite a poison report using `mblock_header_1.sequence` [2](#0-1) . At no point does it compare `mblock_header_1` against `mblock_header_2` for inequality, nor does it check that they even share the same `sequence`/`prev_block` (fields that would normally indicate a genuine fork position).

Contrast this with the mempool-level admission check in `blocks.rs`, which at least checks that `sequence`, `prev_block`, and `version` match between the two headers before accepting the tx into the mempool [3](#0-2)  — but this check also never asserts the headers are *different*, so it too would pass for identical headers, and more importantly it is not a consensus rule: it only gates mempool relay, not what a miner can embed directly in a block or what `process_transaction`/`handle_poison_microblock` will accept during actual state transition.

Attacker's exact input: take any real, previously broadcast `StacksMicroblockHeader` signed by a target miner's microblock key, and construct `TransactionPayload::PoisonMicroblock(hdr.clone(), hdr.clone())`. Sign this with any reporter key and submit as a transaction (via direct block inclusion, bypassing mempool checks, or any path that reaches `process_transaction`).

Exploit flow:
1. `check_microblock_header_signer(hdr, hdr)` → `Ok(pkh)` trivially.
2. `get_microblock_pubkey_hash_height(pkh)` succeeds because `pkh` genuinely was used by the honest miner for one real (non-conflicting) microblock stream.
3. Maturity window check passes if within `MINER_REWARD_MATURITY`.
4. A poison report is inserted for that miner's pubkey height, crediting the attacker/reporter — despite there being only one, legitimately-signed microblock header and zero evidence of a second, conflicting one.

Existing guards that fail to catch this: `check_microblock_header_signer` (checks signer equality only, not header inequality); there is no `validate_*_static` or block-acceptance-time cross-check against the miner's actual, previously stored microblock stream inside `handle_poison_microblock` itself — the function trusts the submitted header pair as self-contained proof.

### Impact Explanation
This directly causes block-reward theft: the coinbase/reward attributable to the targeted, honest miner's block is diverted to the attacker-controlled "reporter" principal via the poison-microblock commission mechanism, with no actual equivocation having occurred. This matches the Critical category "block-reward theft/double-payment/loss" — an honest miner is punished and a false reporter is paid, which is a wrongful, permanent reward misallocation once the transaction is mined and matured.

### Likelihood Explanation
The attacker needs only: (1) to observe any single real, signed microblock header broadcast by any miner (trivially available — all microblocks are public), and (2) the ability to get a transaction included in a block (as an unprivileged participant able to submit transactions/blocks). No majority stake, no signer/validator privilege, and no compromise of the target miner's key are required — the attacker only reuses public data. This is fully repeatable against any miner who has produced at least one microblock, within the `MINER_REWARD_MATURITY` window, and costs only ordinary transaction fees.

### Recommendation
In `check_microblock_header_signer` (or immediately before it in `handle_poison_microblock`), explicitly reject the payload if `mblock_hdr_1 == mblock_hdr_2`, and additionally require that the two headers share the same `sequence` and `prev_block` (as the mempool check already partially does) while differing in some other field (e.g., `tx_merkle_root` or effective block hash), so that the pair constitutes genuine cryptographic proof of equivocation rather than a duplicated single signature.

### Proof of Concept
Rust integration test analogous to `process_poison_microblock_same_block`/`process_poison_microblock_multiple_same_block` in `stackslib/src/chainstate/stacks/db/transactions.rs`:
1. Generate one signed `StacksMicroblockHeader` (`mblock_1`) for a miner key, and register its pubkey hash via `StacksChainState::insert_microblock_pubkey_hash`.
2. Build `TransactionPayload::PoisonMicroblock(mblock_1.header.clone(), mblock_1.header.clone())` (both slots identical — assert `mblock_1.header == mblock_1.header.clone()` to make the broken equality explicit before tracing).
3. Sign and submit via `StacksChainState::process_transaction`.
4. Assert the current code returns `Ok(..)` (successful slash) — demonstrating the vulnerability — versus the expected/fixed behavior of returning an `Err(Error::InvalidStacksTransaction(..))` because the headers are not distinct.
5. Optionally follow through to reward-maturation logic to show the coinbase/reward for that block height is redirected to the reporter despite no real fork.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L722-856)
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
