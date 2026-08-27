### Title
Vote-account VAT (Validator Admission Ticket) burn panics on stale balance snapshot, allowing an ordinary `Withdraw` vote instruction to halt the cluster - (File: runtime/src/bank.rs)

### Summary
`Bank::maybe_burn_vat_from_staked_accounts` deducts a fixed per-epoch amount (`vat_to_burn_per_epoch`) from every staked vote account's lamport balance, using `checked_sub(..).expect(...)` on the assumption that `clone_and_filter_for_vat` has already guaranteed every included account holds enough balance. This assumption can be violated because the balance check happens against a point-in-time snapshot embedded in `VersionedEpochStakes`, while the deduction itself is applied to the **live, current** account fetched with `self.get_account(vote_pubkey)`. Between those two moments, the vote account's authorized withdrawer can submit an ordinary `Withdraw` instruction that drains the account below the VAT threshold, since `withdraw()` in `programs/vote/src/vote_state/mod.rs` never enforces the VAT minimum balance. This produces an underflow that is `.expect()`-panicked instead of being handled, crashing every validator that processes the same deterministic epoch-boundary logic simultaneously.

### Finding Description
`VoteAccounts::clone_and_filter_for_vat` (used to build the epoch's stake/vote-account snapshot) requires `vote_account.lamports() >= minimum_vote_account_balance` at the time the snapshot is taken: [1](#0-0) 

This filtered/snapshotted set becomes part of `VersionedEpochStakes`, which is later consumed by `maybe_burn_vat_from_staked_accounts` to burn `vat_to_burn_per_epoch` lamports from every included account. Crucially, the function re-fetches the account fresh from the bank rather than reusing the already-verified snapshot balance, and assumes via `.expect()` that the fresh balance still satisfies the previously-checked minimum: [2](#0-1) 

Nothing prevents the vote account's balance from dropping below `vat_to_burn_per_epoch` between the time the snapshot/filter was computed and the time the burn is executed. The vote program's `withdraw` instruction — callable by any signer holding the authorized-withdrawer key, i.e. an ordinary client transaction — only enforces rent-exemption and `pending_delegator_rewards`, never the VAT minimum balance: [3](#0-2) 

Because `VersionedEpochStakes` (and the derived `filtered_distribution_vote_accounts`/stake snapshot) is computed at one epoch boundary and only consumed for VAT burning at a later point (per Solana's standard multi-epoch stake-activation pipeline, `epoch_stakes(...)` values are looked up ahead of when they take effect), there is a real window of many slots/epochs during which the withdrawer can act. This is the direct structural analog of the SymmIO bug: a balance is validated at one point, then unconditionally subtracted at another point without re-checking, and the subtraction is performed with a language construct that aborts execution (`.expect()`/underflow panic in Rust vs. Solidity's revert-on-underflow) rather than safely clamping or erroring out gracefully.

### Impact Explanation
Unlike Solidity's revert (which only fails a single liquidation transaction), a Rust panic inside `Bank` processing during the mandatory, consensus-critical epoch-boundary path (`maybe_burn_vat_from_staked_accounts`) is executed identically and deterministically by every validator on the network. All validators that reach this code with the same bank state will panic simultaneously, producing a cluster-wide halt — the most severe accepted impact category in scope (cluster-halting panic).

### Likelihood Explanation
The triggering action — calling the vote program's `Withdraw` instruction as the authorized withdrawer of one's own vote account — is a completely ordinary, unprivileged operation available to any vote-account owner participating in Alpenglow (feature-gated, but not operator-only or mocked-only). No special network position, malicious peer behavior, or privileged access is required; only feature-set activation of `alpenglow` is a precondition, which is a normal, already-in-scope activated code path.

### Recommendation
Do not use an unchecked `.expect()` assumption that a previously-taken snapshot's balance guarantee still holds at burn time. Instead:
- Re-validate the live account's balance against `vat_to_burn_per_epoch` immediately before subtracting, and use `saturating_sub`/clamp-to-zero (burning whatever remains) instead of panicking, or
- Exclude/skip accounts whose live balance is now insufficient rather than asserting an invariant that external, ordinary transactions can invalidate, mirroring the SymmIO fix pattern of clamping the deducted amount to the available balance instead of assuming it will always be sufficient.

### Proof of Concept
1. Vote account `V` is staked and, at the epoch-stake computation snapshot, has balance `>= minimum_vote_account_balance_for_vat()` and thus passes `clone_and_filter_for_vat`, landing in the `VersionedEpochStakes` for a future epoch.
2. Before that future epoch's boundary processing runs `maybe_burn_vat_from_staked_accounts`, the authorized withdrawer of `V` submits an ordinary `Withdraw` instruction (`programs/vote/src/vote_state/mod.rs::withdraw`) reducing `V`'s balance to just above the rent-exempt minimum (this passes all existing checks since VAT minimum is not enforced there).
3. When the epoch boundary is reached, `maybe_burn_vat_from_staked_accounts` iterates the (stale) filtered vote-account set, fetches `V`'s current (now-reduced) balance via `self.get_account(vote_pubkey)`, and executes `checked_sub(vat_to_burn_per_epoch).expect(...)`.
4. Since `V`'s live balance is now less than `vat_to_burn_per_epoch`, `checked_sub` returns `None`, and `.expect()` panics — crashing the validator process. Because this code executes deterministically on every validator processing the same epoch boundary, the entire cluster halts simultaneously.

### Citations

**File:** vote/src/vote_account.rs (L220-231)
```rust
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
```

**File:** runtime/src/bank.rs (L2687-2702)
```rust
        // Vote accounts have already been filtered by clone_and_filter_for_vat to only include
        // accounts with non-zero stake and sufficient balance.
        for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
            let mut account = self.get_account(vote_pubkey).unwrap();
            total_vat += vat_to_burn_per_epoch;
            account.set_lamports(
                account
                    .lamports()
                    .checked_sub(vat_to_burn_per_epoch)
                    .expect(
                        "Vote accounts should have already been filtered to contain enough \
                         balance for the VAT",
                    ),
            );
            accounts_to_store.push((*vote_pubkey, account));
        }
```

**File:** programs/vote/src/vote_state/mod.rs (L1079-1122)
```rust
    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;

    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
    }
```
