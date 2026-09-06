### Title
Missing sequence-equality / hash-divergence check in `check_microblock_header_signer` allows a false "double-signature" poison report against an honest miner - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`check_microblock_header_signer` only verifies that the two microblock headers in a `TransactionPayload::PoisonMicroblock` recover to the same public key hash; it never checks that the two headers share the same `sequence` (the only condition that actually proves the signer equivocated) nor that they differ in content at that same slot. `handle_poison_microblock` then accepts and stores the report unconditionally on that basis, allowing any two genuinely sequential (non-forked) microblocks signed honestly by the same miner key to be misrepresented as proof of a fork.

### Finding Description
The equality that should hold is: *"reported double-signature" == "two headers at the SAME sequence, signed by the SAME key, with DIFFERENT block hashes"*. The code only enforces the "same key" part: [1](#0-0) 

`check_microblock_header_signer` recovers `pkh1` and `pkh2` from `mblock_hdr_1`/`mblock_hdr_2` and only errors if `pkh1 != pkh2`. There is no comparison of `mblock_hdr_1.sequence` vs `mblock_hdr_2.sequence`, and no comparison of the header/block hashes to confirm they diverge at the same point in the stream.

`handle_poison_microblock` calls this check and then proceeds directly to look up the pubkey-hash registration height, check the `MINER_REWARD_MATURITY` window, and store the report via `insert_microblock_poison`, using only `mblock_header_1.sequence` as "the sequence at which the fork occurred": [2](#0-1) 

Because two microblocks produced honestly and sequentially by the same miner (e.g., sequence 5 and sequence 6 of the same stream) are trivially signed by the same key, and the code never requires `mblock_header_1.sequence == mblock_header_2.sequence` (nor that they represent a genuine branching at one sequence with two different bodies), an unprivileged reporter can submit any two of a miner's own legitimately-produced, sequential microblock headers as a "poison" pair. The check passes on same-pubkeyhash grounds alone, the pubkey hash is still registered and within `MINER_REWARD_MATURITY` (as required by the preconditions), and `insert_microblock_poison` records a punishment that later redirects the honest miner's coinbase/fees to the reporter — despite no equivocation ever having occurred.

### Impact Explanation
This breaks the intended invariant that poison-microblock punishment only fires on genuine equivocation (same sequence, two different signed bodies). The consequence is block-reward theft: an honest miner's coinbase/fee reward is slashed and paid out via `poison_microblock_commission` to an attacker who fabricated the "fork" from the miner's own legitimate, sequential microblocks. This is a Critical-category impact (block-reward theft/mis-payment) directed at an honest party's funds, and it is repeatable against any miner whose microblock pubkey hash is still within the maturity window — it requires no majority stake, no signer collusion, and no compromise of the miner's key.

### Likelihood Explanation
The attacker only needs to be a normal network participant able to observe an honest miner's own broadcast microblocks (which are public) and submit a transaction — no elevated privilege, no majority stake, no BTC spend beyond a normal transaction fee. The only preconditions are that the target's microblock pubkey hash is registered (`insert_microblock_pubkey_hash`, i.e., the miner produced at least one microblock stream) and still within `MINER_REWARD_MATURITY` blocks of its registration — both are routine, unavoidable conditions for any active miner. This makes the attack highly feasible and repeatable against essentially every miner who ever produces more than one microblock.

### Recommendation
In `check_microblock_header_signer` (or immediately in `handle_poison_microblock` before storing the report), additionally require:
1. `mblock_header_1.sequence == mblock_header_2.sequence`, and
2. the two headers hash to different values (`mblock_header_1 != mblock_header_2` / differing signatures over the same sequence),
before accepting the pair as proof of equivocation. Only when both headers occupy the identical position in the microblock stream but differ in content does same-pubkeyhash recovery constitute proof of a fork.

### Proof of Concept
Rust integration test plan:
1. Generate one miner keypair; construct an honest, valid microblock stream with `StacksMicroblockHeader` at `sequence = 5` and `sequence = 6`, both properly signed by the same key, each with distinct, valid `prev_block`/`tx_merkle_root` (a normal, non-forking sequential stream).
2. Register the miner's microblock pubkey hash via the normal block-commit path (`insert_microblock_pubkey_hash`) and advance the chain to a height still within `MINER_REWARD_MATURITY`.
3. Construct and broadcast a `TransactionPayload::PoisonMicroblock(header_seq5, header_seq6)` from an unrelated reporter account.
4. Assert the transaction is **accepted** (call `handle_poison_microblock` / process the tx) even though `header_seq5.sequence != header_seq6.sequence` and there was no genuine equivocation — this demonstrates `check_microblock_header_signer` returning `Ok` incorrectly.
5. Mature the reward and call `find_mature_miner_rewards`; assert that the reporter is paid `poison_microblock_commission` and the miner's coinbase/fees are slashed, i.e., assert `TENURE_REWARD paid to reporter == coinbase_and_fees_earned_by_honest_miner`, proving the equality "reward paid == reward due to an actual cheater" fails for an honest miner who never double-signed.

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
