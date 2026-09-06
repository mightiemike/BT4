### Title
`handle_poison_microblock` accepts non-conflicting microblock headers from a linear stream as valid equivocation evidence, causing wrongful miner-reward forfeiture - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`handle_poison_microblock` only verifies that `mblock_header_1` and `mblock_header_2` were signed by the same microblock key via `check_microblock_header_signer` (`pkh1 == pkh2`), and never checks `sequence`, `prev_block`, or `version` equality between the two headers. Since an honest miner signs every microblock in a tenure with the same key, any two headers from that miner's legitimate, non-forking stream trivially satisfy this check, letting an attacker submit them as "poison" evidence and steal a commission from that miner's coinbase.

### Finding Description
The broken equality is: **"poison-microblock evidence proves equivocation (two conflicting microblocks at the same point in the stream)"** vs. what the code actually verifies: **"the two headers were signed by the same key"** — `pkh1 == pkh2` only.

`handle_poison_microblock` (`stackslib/src/chainstate/stacks/db/transactions.rs:722-856`) calls `check_microblock_header_signer` which recovers each header's public key hash and errors only if `pkh1 != pkh2` [1](#0-0) . It never compares `mblock_header_1.sequence`, `.prev_block`, or `.version` against `mblock_header_2`'s. It then unconditionally records a poison report keyed on `mblock_header_1.sequence` [2](#0-1) .

By contrast, the *mempool-only* gate in `blocks.rs::mempool_storage_check_tx` (the `MemPoolRejection` path) does enforce `sequence`, `prev_block`, and `version` equality before checking `pkh1 == pkh2` [3](#0-2) . This is a mempool admission filter, not a state-transition rule enforced by `process_transaction` / `run_poison_microblock` / `handle_poison_microblock`. Any path that reaches `process_transaction` without going through `mempool_storage_check_tx` (e.g., a miner including a transaction directly into a block, or an attacker mining their own tenure and including the transaction themselves) bypasses this check entirely, as the existing test suite confirms — `process_transaction` is exercised directly without going through the mempool rejection gate in `stackslib/src/chainstate/stacks/db/transactions.rs:5549-5560` [4](#0-3) .

Because a single miner signs an *entire tenure's* microblock stream with one microblock private key, `pkh1 == pkh2` is true for **any two headers from that miner's stream**, whether or not they conflict. The attacker's exact input is two honestly-produced, non-conflicting headers (e.g., seq=3 and seq=91) from the linearly-chained stream, wrapped in a `TransactionPayload::PoisonMicroblock` and included in a block they mine (or relayed to a miner who fails to re-run mempool validation on it). `handle_poison_microblock` will accept this and record a poison report.

The downstream consequence is real reward loss: `find_mature_miner_rewards` looks up `get_poison_microblock_report` at the matured reward height and, if present, redirects the coinbase reward via `calculate_miner_reward` — the reporter gets `POISON_MICROBLOCK_COMMISSION_FRACTION` (5%) of the coinbase, and the rest is destroyed instead of paid to the honest miner [5](#0-4) [6](#0-5) .

### Impact Explanation
This is block-reward theft: an honest miner who never equivocated has their coinbase reward destroyed and a 5% commission diverted to an attacker who fabricated "evidence" from two ordinary, non-conflicting microblocks. This matches the Critical category "block-reward theft/double-payment/loss" — a non-equivocation is treated as a slashable equivocation, and funds are misdirected/burned that should have gone to the honest miner.

### Likelihood Explanation
The attacker needs only: (1) two legitimately-signed microblock headers from any honest miner's public tenure stream (trivially obtainable by observing the network), and (2) the ability to get a `PoisonMicroblock` transaction included in a block — either by mining their own tenure (a single miner slot, no majority stake required) or by relaying it to another miner who does not itself re-run the mempool-only conflict check before inclusion. No signer majority, no privileged role, and no coincidence of `sequence` values is required, since the code never checks that. This is repeatable against every miner whose microblock stream can be observed, for as long as microblocks are supported by the state-transition logic in this codebase.

### Recommendation
Move the mempool-only conflict checks into `handle_poison_microblock` (or `check_microblock_header_signer`) itself, so the state-transition path enforces `mblock_header_1.sequence == mblock_header_2.sequence`, `mblock_header_1.prev_block == mblock_header_2.prev_block`, and `mblock_header_1.version == mblock_header_2.version` in addition to `pkh1 == pkh2`, mirroring the check currently only present in `blocks.rs:6845-6849`, and reject the transaction as invalid otherwise regardless of how it entered the block (mempool or direct inclusion).

### Proof of Concept
Rust integration test plan:
1. Generate a microblock signing key `block_privk` and produce a genuinely linear, non-forking stream of microblocks (e.g., 100 microblocks, seq 0..99), each correctly chained via `prev_block` and each signed by `block_privk`.
2. Take `mblock_header_1 = stream[3].header` and `mblock_header_2 = stream[91].header` — both real, honestly produced, non-conflicting headers (different `sequence`, different `prev_block`).
3. Build `TransactionPayload::PoisonMicroblock(mblock_header_1.clone(), mblock_header_2.clone())`, sign it with an attacker-controlled reporter key, and call `StacksChainState::process_transaction` directly (bypassing `mempool_storage_check_tx`/`MemPoolRejection`), as done in the existing test harness pattern at `stackslib/src/chainstate/stacks/db/transactions.rs:5549-5560`.
4. **Assert the equality that should hold but doesn't**: assert `mblock_header_1.sequence != mblock_header_2.sequence` (3 != 91) — i.e., confirm no coincidence of sequence exists — yet also assert `process_transaction` returns `Ok(..)` and `StacksChainState::get_poison_microblock_report(..)` returns `Some((reporter_addr, 3))`.
5. Continue chainstate to the matured reward height and assert the honest miner's account balance reflects the forfeited coinbase (reduced/zeroed) and the reporter account received the 5% commission, despite no actual equivocation ever having occurred in the underlying stream.

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

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-855)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5548-5560)
```rust
            // process it!
            let (fee, receipt) = StacksChainState::process_transaction(
                &mut conn,
                &signed_tx_poison_microblock,
                false,
                None,
            )
            .unwrap();

            // there must be a poison record for this microblock, from the reporter, for the microblock
            // sequence.
            let report_opt = StacksChainState::get_poison_microblock_report(&mut conn, 1).unwrap();
            assert_eq!(report_opt.unwrap(), (reporter_addr.clone(), 123));
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-896)
```rust
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
