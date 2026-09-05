### Title
PoisonMicroblock reward is not bound to the discovering reporter, allowing front-running theft of the coinbase commission - (File: stackslib/src/chainstate/stacks/db/transactions.rs)

### Summary
`handle_poison_microblock()` pays a 5% coinbase commission to whichever principal's `PoisonMicroblock` transaction is first mined with the lowest microblock `sequence` for a given pubkey-hash fork. The "proof" consumed here — two microblock headers signed by the same key at diverging sequences — is entirely public data once broadcast (either via microblock relay or via the pending `PoisonMicroblock` transaction sitting in the mempool). An attacker who observes another party's pending `PoisonMicroblock` transaction can copy the two header structs into their own self-authored transaction, attach a higher fee, and have it mined first, thereby stealing the reward that should have gone to the original discoverer. This is the same bug class as `reportUnauthorizedSigning()` in keep-core: the reward-bearing evidence is not cryptographically bound to the reporter who discovered it, so whoever "gets there first" (by fee/priority, not by discovery) collects the reward.

### Finding Description
`StacksChainState::check_microblock_header_signer()` [1](#0-0)  only verifies that both microblock headers recover to the same public key hash; it performs no binding to `tx-sender`/`invoke_ctx.sender`. The reward-crediting logic in `handle_poison_microblock()` then simply records whichever `sender_principal` transaction is processed first for a given `(mblock_pubk_height, sequence)`, using strict "lower sequence wins" tie-breaking: [2](#0-1) 

Because `mblock_header_1` and `mblock_header_2` are just publicly-signed microblock headers (not a secret tied to the reporter), any principal can:
1. Observe a legitimate discoverer's yet-unmined `PoisonMicroblock` transaction in the mempool (or observe the conflicting microblocks directly on the wire), and
2. Construct their own `PoisonMicroblock` transaction with the identical `mblock_header_1`/`mblock_header_2` payload, sign it with their own key, attach a higher fee, and get it mined first.

The coinbase reward diversion this triggers is implemented in `StacksChainState::calculate_miner_reward()`, where the recorded reporter (not necessarily the true discoverer) receives `POISON_MICROBLOCK_COMMISSION_FRACTION` (5%) of the miner's coinbase, while the rest is destroyed: [3](#0-2)  and the actual reporter used for maturation is looked up purely from the stored `(reporter, seq)` record with no additional authentication: [4](#0-3)  The commission fraction constant confirms the reward is a fixed 5% cut, mirroring the keep-core report's 5% figure: [5](#0-4) 

This breaks the intended equality "the party that discovered the fraud proof == the party rewarded for it." Instead the actual invariant enforced is "the party whose transaction is mined first for a given/lower sequence == the party rewarded," which is trivially manipulable by anyone monitoring the mempool, since the underlying evidence carries no reporter-specific binding (e.g., no signature over `tx-sender` or commit-reveal scheme).

### Impact Explanation
This matches the explicitly allowed "poison or reward mis-payment bounded to fees" High-impact category. The blast radius is bounded: at most the 5% coinbase commission for a single tenure/coinbase maturation event is misdirected from the true discoverer to a front-runner. It does not cause a chain split, invalid-block acceptance, or double-spend of the full block reward — only a reward-recipient mismatch bounded by the size of a single coinbase commission. It is minority/unprivileged triggerable: any single actor monitoring the mempool, with no stake or admin privilege, can perform this attack.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: `PoisonMicroblock` transactions must be broadcast and included via the normal mempool/relay path before being mined, so their content (the two conflicting microblock headers) is visible to any node or relay participant before confirmation. An attacker running a mempool-watching bot can trivially extract the header pair and resubmit with a higher fee, exactly analogous to standard MEV/front-running. The fact that the underlying evidence is unforgeable (it must reference a real fork) does not prevent theft of *credit* for the reward, only theft of the ability to *fabricate false* fraud.

### Recommendation
Bind the poison-microblock reward eligibility to the discoverer rather than to whichever transaction is mined first with matching header content. Options consistent with the keep-core fix:
- Require the `PoisonMicroblock` payload to include a signature/commitment over `tx-sender` (e.g., a detached signature over the reporter's own principal plus the fork evidence), so that copying the raw header pair into a different sender's transaction fails verification.
- Alternatively, adopt a commit-reveal scheme: the discoverer submits a hash commitment of the evidence in one transaction/block, then reveals the full header pair in a later transaction, so that mempool observers cannot extract and resubmit the evidence before the original discoverer's transaction confirms.

### Proof of Concept
1. Alice observes a microblock fork (two microblocks at the same sequence, signed by the same key, indicating a leaked/reused microblock signing key) and constructs `tx_poison_microblock_A = PoisonMicroblock(mblock_header_1, mblock_header_2)`, signs it with her own key, and broadcasts it with a normal fee.
2. Bob, monitoring the mempool, sees `tx_poison_microblock_A`, extracts `mblock_header_1`/`mblock_header_2` verbatim, and constructs `tx_poison_microblock_B = PoisonMicroblock(mblock_header_1, mblock_header_2)` signed with his own key and a higher fee.
3. Because `check_microblock_header_signer()` only checks that both headers recover to the same pubkey hash (not who submitted them) [1](#0-0) , and `handle_poison_microblock()` records whichever sender's transaction is processed first at the given/lower sequence [2](#0-1) , if `tx_poison_microblock_B` is mined before/instead of `tx_poison_microblock_A` (e.g., in the same block with higher fee/priority, or in an earlier block), Bob's principal is recorded as the `reporter`.
4. When the punished miner's coinbase matures, `find_mature_miner_rewards()`/`calculate_miner_reward()` pay the 5% commission to Bob's address instead of Alice's [6](#0-5) , even though Alice was the true discoverer of the fraud.

**Note on confidence**: I was unable to fully trace mempool-ordering/fee-prioritization guarantees for the Nakamoto consensus path in this snapshot (only the epoch2.x `handle_poison_microblock`/`calculate_miner_reward` code paths were directly available via search), so I cannot confirm whether this mechanism is still reachable/active under the current Nakamoto rules or is legacy-only code retained for epoch2.x blocks. If it is legacy-only and unreachable post-Nakamoto, the practical severity would be lower than stated.

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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-904)
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

**File:** stackslib/src/chainstate/stacks/db/mod.rs (L936-939)
```rust
pub use stacks_common::consts::MINER_REWARD_MATURITY;

// fraction (out of 100) of the coinbase a user will receive for reporting a microblock stream fork
pub const POISON_MICROBLOCK_COMMISSION_FRACTION: u128 = 5;
```
