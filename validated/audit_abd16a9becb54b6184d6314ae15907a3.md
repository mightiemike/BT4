### Title
`handle_poison_microblock` accepts any two same-key-signed microblock headers as a valid poison report without checking sequence equality, prev_block match, or block-hash divergence — allowing coinbase theft/mis-slashing without a genuine equivocation - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
The consensus-level `handle_poison_microblock` function that actually processes a `PoisonMicroblock` transaction inside a block never re-derives or re-checks the equivocation condition (`sequence_1 == sequence_2 && block_hash_1 != block_hash_2`) that `StacksChainState::validate_parent_microblock_stream` uses to detect real forks. It only verifies that both headers recover to the same signer pubkey hash and that pubkey hash was seen on-chain within the maturity window. The equality assumed by the design — "accepted poison report" == "output of the real fork-detector" — is not enforced at this layer.

### Finding Description
The claimed equality is:
`{headers accepted by handle_poison_microblock}` == `{headers that validate_parent_microblock_stream would emit as (prior, cur) with prior.header.sequence == cur.header.sequence && prior.block_hash() != cur.block_hash()}` (blocks.rs:2992–3037 shows the real fork-detector requiring duplicate `prev_block`/matching sequence to declare a fork).

`handle_poison_microblock` (transactions.rs:722–856) only performs:
- `check_microblock_header_signer` (transactions.rs:686–713), which recovers the pubkeys of `mblock_header_1` and `mblock_header_2` and requires they match each other.
- `get_microblock_pubkey_hash_height` lookup + `MINER_REWARD_MATURITY` window check.
- Book-keeping of whichever of the two submitted sequence numbers (`mblock_header_1.sequence`) is lower than any prior report.

Nowhere in this function is `mblock_header_1.sequence == mblock_header_2.sequence`, `prev_block` equality, or `block_hash()` divergence checked. The only place in the codebase that enforces those structural constraints is the *mempool* admission check in blocks.rs:6844–6861 (`TransactionPayload::PoisonMicroblock` arm, returning `MemPoolRejection::PoisonMicroblocksDoNotConflict`), which is a mempool-entry gate, not a block-processing/consensus check re-invoked by `process_transaction_payload`/`handle_poison_microblock`.

Consequently, a block producer (miner) can hand-craft a `PoisonMicroblock` transaction from two **genuinely, validly signed** microblock headers by some honest miner's real key — e.g., two headers taken from different, non-conflicting positions of the same canonical (non-forked) stream, or otherwise not satisfying the true duplicate-sequence/divergent-hash fork condition — and include it directly in their own self-mined block, bypassing the mempool's `PoisonMicroblocksDoNotConflict` gate entirely. Since `handle_poison_microblock` never re-derives the fork condition, the transaction is accepted by every node that processes the block (all nodes run identical, deterministic logic), and the honest miner's coinbase for that tenure is unjustly forfeited via `calculate_miner_reward`'s `punished` branch (accounts.rs:872–904), with `poison_microblock_commission` (accounts.rs:794–797) paid to the attacker's reporter address instead.

### Impact Explanation
This is not a chain split (all nodes agree deterministically on the same, incorrect outcome), but it is a reward mis-payment: an honest miner's coinbase is forfeited and a fraction of it (`POISON_MICROBLOCK_COMMISSION_FRACTION`) is paid to the attacker, without any real equivocation having occurred, as long as the attacker can obtain (not forge) two validly-signed headers from a target miner's key that do not actually satisfy the fork condition. This maps to the "High" category in the rubric: "a poison or reward mis-payment bounded to fees" — bounded to the coinbase/commission amount, not a network-wide invalid-block-accept or fork.

### Likelihood Explanation
The attacker needs: (1) at least one won sortition slot (to include a self-crafted transaction directly in a block, bypassing mempool checks), and (2) two validly-signed microblock headers from some other miner's registered microblock pubkey hash within the `MINER_REWARD_MATURITY` window — which are broadcast/public data, not secret. No majority stake, no signer compromise, and no private key forgery are required; this fits the "single miner slot" attacker model. This is repeatable against any miner whose microblock-signing key and any two headers (from any stream position, not necessarily a real fork) can be obtained before the maturity window closes.

### Recommendation
In `handle_poison_microblock` (transactions.rs:722), before recording a report, re-derive and enforce the actual equivocation invariant: require `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, matching `version`, and `mblock_header_1.block_hash() != mblock_header_2.block_hash()` (mirroring the check currently only present in the mempool-only path at blocks.rs:6844–6861). This closes the gap between block-inclusion-time consensus validation and mempool admission-time validation.

### Proof of Concept
Rust integration test plan (extending the existing `process_poison_microblock_invalid_transaction`/`process_poison_microblock_multiple_same_block` tests in transactions.rs:5599+):
1. Register a legitimate microblock pubkey hash for `block_privk` at some height via `StacksChainState::insert_microblock_pubkey_hash`.
2. Construct two microblock headers signed by the SAME `block_privk`, but with **different sequence numbers** (e.g., `seq=5` and `seq=6`) and non-matching `prev_block` (i.e., not a genuine fork per `validate_parent_microblock_stream`'s semantics — assert independently that calling `validate_parent_microblock_stream` on a stream containing both headers returns `None`/no poison payload).
3. Build a `TransactionPayload::PoisonMicroblock(header_seq5, header_seq6)` transaction, sign it with an attacker/reporter key, and call `StacksChainState::process_transaction` (which internally reaches `process_transaction_payload` → `handle_poison_microblock`) directly — never routing through mempool admission (`mempool_storage_check`/`PoisonMicroblocksDoNotConflict`).
4. Assert LHS: `validate_parent_microblock_stream(...)` on the same two headers returns `None` (no genuine fork detected).
5. Assert RHS: `StacksChainState::process_transaction(...)` returns `Ok(...)` and `StacksChainState::get_poison_microblock_report(...)` shows a report recorded for the attacker/reporter.
6. The mismatch between step 4 (`None`) and step 5 (`Ok`, report recorded) demonstrates the broken equality — an "accepted poison report" that never went through, and would fail, the real fork-detector. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L2992-3021)
```rust
        // sanity check -- all parent block hashes are unique.  If there are duplicates, then the
        // miner equivocated.
        let mut parent_hashes: HashMap<BlockHeaderHash, StacksMicroblockHeader> = HashMap::new();
        for (i, signed_microblock) in signed_microblocks.iter().enumerate() {
            if let Some(conflicting_microblock_header) =
                parent_hashes.get(&signed_microblock.header.prev_block)
            {
                debug!(
                    "Deliberate microblock fork: duplicate parent {}",
                    signed_microblock.header.prev_block
                );

                return Some((
                    i - 1,
                    Some(TransactionPayload::PoisonMicroblock(
                        signed_microblock.header.clone(),
                        conflicting_microblock_header.clone(),
                    )),
                ));
            }
            parent_hashes.insert(
                signed_microblock.header.prev_block.clone(),
                signed_microblock.header.clone(),
            );
        }

        // hashes are contiguous enough -- for each seqnum, there is a microblock with seqnum+1 with the
        // microblock at seqnum as its parent.  There may be more than one.
        let mut prior_microblock = first_microblock;
        for (j, cur_microblock) in signed_microblocks.iter().skip(1).enumerate() {
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L861-904)
```rust
        // each participant gets a share of the coinbase proportional to the fraction it burned out
        // of all participants' burns.
        let coinbase_reward = participant
            .coinbase
            .checked_mul(this_burn_total)
            .expect("FATAL: STX coinbase reward overflow")
            / burn_total;

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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1027-1052)
```rust
        // was this block penalized for mining a forked microblock stream?
        // If so, find the principal that detected the poison, and reward them instead.
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);

        if let Some(ref _poison_reporter) = poison_recipient_opt.as_ref() {
            test_debug!(
                "Poison-microblock reporter {} at height {}",
                &_poison_reporter.to_string(),
                reward_height
            );
        } else {
            test_debug!("No poison-microblock report at height {}", reward_height);
        }

        // calculate miner reward
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );
```
