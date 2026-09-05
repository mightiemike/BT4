### Title
PoisonMicroblock reporter reward can be stolen by the block-producing miner via evidence copying - (File: `stackslib/src/chainstate/stacks/db/transactions.rs`)

### Summary
`StacksChainState::handle_poison_microblock` grants the "poison-microblock" commission (a slice of the punished miner's coinbase) to whichever transaction sender is recorded as having reported the lowest-sequence conflicting microblock headers for a given microblock public key. The function only validates that `mblock_header_1`/`mblock_header_2` are signed by the same key and compares the claimed `sequence` number; it never binds the reward to the party who *originally discovered* the fork. Because the evidence (`mblock_header_1`, `mblock_header_2`) is fully public payload data, whoever gets a `PoisonMicroblock` transaction carrying that exact evidence included in a block is credited as the `reporter_principal` and receives the commission [1](#0-0) .

### Finding Description
`handle_poison_microblock` derives `sender_principal`/`reporter_principal` purely from the transaction's `invoke_ctx.sender` (i.e., whoever authored/signed the enclosing `PoisonMicroblock` transaction), and stores it as the winning reporter whenever the supplied `sequence` beats (is lower than) the currently recorded one [2](#0-1) . There is no check that this sender is the party who actually detected the fork or produced the evidence — anyone able to reconstruct the two conflicting `StacksMicroblockHeader`s (which are broadcast, public microblock data, or are visible once someone else's `PoisonMicroblock` transaction has been relayed but not yet mined) can wrap that identical evidence in their own signed transaction and submit it.

The block-producing miner for the next anchor block or microblock is in a privileged position here: upon observing a valid `PoisonMicroblock` transaction from another party in the mempool, the miner can simply omit it and instead insert their own transaction carrying the same `mblock_header_1`/`mblock_header_2`, naming themselves as sender. Since `handle_poison_microblock` only compares `sequence` values and does not track transaction origin identity beyond the immediate caller, the copying transaction is accepted identically and the miner is recorded as `reporter_principal`.

This reward is subsequently paid out in `calculate_miner_reward`, which pays `poison_microblock_commission(coinbase_reward)` to `reporter_address` — the address recorded by `handle_poison_microblock` — while destroying the rest of the punished miner's coinbase [3](#0-2) . The code comment itself acknowledges the "anyone can present evidence" design, but does not defend against the evidence being copied by whoever controls transaction ordering/inclusion for that block.

### Impact Explanation
This is a minority-triggerable reward mis-payment: a single block producer (no majority or collusion required) can, on observing genuine poison-microblock evidence submitted by anyone else, systematically redirect the associated coinbase commission to themselves instead of the actual discoverer, by re-submitting the same public evidence under their own principal before/instead of the original transaction. The equality broken is "the reporter recorded on-chain == the party who legitimately discovered/first-broadcast the fork evidence" — this repo only enforces "the reporter recorded == whoever's transaction with valid (headers, sequence) landed first," which any observer of pending evidence, especially the assembling miner, can hijack. The result is a bounded reward misdirection (a fraction of a coinbase reward) but does not affect state-root agreement or block acceptance across the network.

### Likelihood Explanation
Any single miner assembling an anchor block that also sees or independently reconstructs valid poison-microblock evidence has both the motive (claim the commission) and the mechanism (control over which transactions are included and in what order) to exploit this without needing any privileged key, admin cooperation, or majority coordination — a straightforward mempool observation and transaction substitution.

### Proof of Concept
1. Miner M forks the microblock stream while producing tenure `T` and later broadcasts a conflicting microblock (creates two headers `H1`, `H2` signed by the same key, with `H2.sequence < H1.sequence`).
2. Honest party Alice observes both microblocks, and submits a `PoisonMicroblock` transaction with `(mblock_header_1=H2, mblock_header_2=H1)` to earn the reporter commission.
3. Before including Alice's transaction, a different miner Bob (assembling the next anchor block) sees Alice's pending transaction, extracts `H1`/`H2` from its payload, and crafts and includes his own `PoisonMicroblock` transaction with the same header pair, signed by his own key.
4. `handle_poison_microblock` processes Bob's transaction: `check_microblock_header_signer` validates the same-key signature [4](#0-3) , and since no prior report exists yet (or Bob's is inserted first), Bob is recorded as `reporter_principal` at `mblock_header_1.sequence` [5](#0-4) .
5. At reward maturity, `calculate_miner_reward` pays the `poison_microblock_commission` to Bob's address, not Alice's, even though Alice discovered and first attempted to report the fork [6](#0-5) .

### Recommendation
Bind the poison-microblock commission to a durable, unforgeable claim on the original discovery — e.g., require the reporting transaction to reference/consume a commitment (hash) that was itself timestamped earlier (a two-phase "commit evidence hash, then reveal" scheme), or otherwise ensure the recorded `reporter_principal` cannot be trivially substituted by whoever controls block/transaction ordering for evidence that is already public.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-752)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-895)
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
```
