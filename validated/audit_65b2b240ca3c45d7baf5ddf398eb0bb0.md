### No vulnerability found for this question.

**Rationale:** The report-resolution logic in `stackslib/src/chainstate/stacks/db/transactions.rs` is deterministic, not "last-write-wins." When a `PoisonMicroblock` transaction is processed, the code reads the existing report via `get_microblock_poison_report` and only overwrites it if the new sender proves a fork at a strictly *lower* sequence number than the currently stored one; otherwise the existing report is left untouched [1](#0-0) . This means two different principals filing conflicting poison reports for the same microblock-fork height do not produce ambiguity — whichever principal can present cryptographic proof (two validly signed, conflicting microblock headers) of the *earliest* fork point in the stream wins the recorded report, regardless of submission order in time.

This exact scenario — two different reporters submitting reports for the same height, one at sequence 123 and a later one at sequence 122 — is covered by an existing test, which asserts that the second (lower-sequence) reporter overrides the first, and this is the intended outcome, not a bug [2](#0-1) .

Critically, an attacker cannot fabricate an arbitrarily low sequence to steal the commission: the recorded `seq` is `mblock_header_1.sequence`, taken directly from the two conflicting, validly-signed microblock headers supplied in the transaction payload [3](#0-2) . Producing a report at a given sequence requires possessing two genuinely conflicting signed microblocks at that sequence — this can't be forged without the miner's microblock signing key, so an unprivileged attacker without that key cannot manufacture a "better" (lower) report to steal a legitimate reporter's commission.

Downstream, `get_poison_microblock_report`/`get_microblock_poison_report` simply return whatever single row is stored for that height [4](#0-3) , and `find_mature_miner_rewards` uses that single, deterministically-resolved reporter to compute `poison_recipient_opt`, which `calculate_miner_reward` then pays via `poison_microblock_commission` [5](#0-4) [6](#0-5) . There is no window where two conflicting reports at the same height/sequence combination both persist or where the "wrong" party is chosen non-deterministically — the system always converges on the report proving the earliest fork point, which is the well-defined, intended winner. The premised equality break (legitimate first reporter vs. attacker-controlled last writer) does not hold under this mechanism.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-841)
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
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L5799-5815)
```rust
            let report_opt = StacksChainState::get_poison_microblock_report(&mut conn, 1).unwrap();
            assert_eq!(report_opt.unwrap(), (reporter_addr_1.clone(), 123));

            // process the second one!
            let (fee, receipt) = StacksChainState::process_transaction(
                &mut conn,
                &signed_tx_poison_microblock_2,
                false,
                None,
            )
            .unwrap();

            // there must be a poison record for this microblock, from the reporter, for the microblock
            // sequence.  Moreover, since the fork was earlier in the stream, the second reporter gets
            // it.
            let report_opt = StacksChainState::get_poison_microblock_report(&mut conn, 1).unwrap();
            assert_eq!(report_opt.unwrap(), (reporter_addr_2.clone(), 122));
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L693-704)
```rust
    /// Find the reported poison-microblock data for this block
    /// Returns None if there are no forks.
    pub fn get_poison_microblock_report<T: ClarityConnection>(
        clarity_tx: &mut T,
        height: u64,
    ) -> Result<Option<(StacksAddress, u16)>, Error> {
        let principal_seq_opt = clarity_tx
            .with_clarity_db_readonly(|ref mut db| db.get_microblock_poison_report(height as u32))
            .map_err(|e| Error::ClarityError(e.into()))?;

        Ok(principal_seq_opt.map(|(principal, seq)| (principal.into(), seq)))
    }
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
