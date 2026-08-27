### Title
Unprivileged `DepositDelegatorRewards` can grief a vote account's authorized withdrawer by blocking full withdrawal - (File: programs/vote/src/vote_state/mod.rs)

### Summary
The vote program's `DepositDelegatorRewards` instruction (SIMD-0123) allows any unprivileged account to deposit lamports into an arbitrary vote account, incrementing that account's `pending_delegator_rewards` field. Separately, `withdraw()` refuses a full account close (and, per the account's own test suite, limits partial withdrawals) whenever `pending_delegator_rewards > 0`. Because any signer can trigger the deposit, and clearing of `pending_delegator_rewards` only happens through the block-revenue-sharing distribution machinery (not by the withdrawer directly), a malicious unprivileged actor can force a vote account's `pending_delegator_rewards` to a nonzero value at will, temporarily denying the account's authorized withdrawer the ability to fully close/withdraw the account.

### Finding Description
`VoteInstruction::DepositDelegatorRewards` is processed with only the *source* account required to sign — the vote account itself does not need to co-sign, and there is no privileged/owner check on who may call it: [1](#0-0) 

The handler `deposit_delegator_rewards` verifies only that the *source* is a valid signer, then CPIs a system transfer from the source into the vote account and unconditionally increments `pending_delegator_rewards`: [2](#0-1) 

The vote account's `withdraw()` logic treats `pending_delegator_rewards` as a reserved balance that must not be spent by the withdrawer, and explicitly blocks a full account close ("deinit") while `pending_delegator_rewards > 0`, as demonstrated by the module's own test: [3](#0-2) [4](#0-3) 

`pending_delegator_rewards` is only decremented as part of the block-revenue-sharing distribution path in `runtime/src/bank/fee_distribution.rs`, i.e., it is drained asynchronously by protocol-driven distribution logic, not by an instruction the withdrawer themselves can invoke on demand: [5](#0-4) 

This mirrors the Althea bug class: an unprivileged actor calls a "start distribution" style operation (here, `DepositDelegatorRewards` with even 1 lamport) that flips a lock-like condition (`pending_delegator_rewards != 0`) which blocks a legitimate, unrelated actor (the vote account's authorized withdrawer) from completing an operation (`withdraw`'s full-close path) until the pending amount is cleared through the separate, periodic distribution mechanism.

### Impact Explanation
This is a griefing/temporary-DoS vector on validator vote-account withdrawal rather than a direct theft of funds: an attacker with a trivial system-owned account and minimal lamports can repeatedly re-arm `pending_delegator_rewards` on a target vote account (each deposit is a no-op-cost transfer to themselves-controlled destination they don't own, but is cheap), preventing the vote account's authorized withdrawer from fully closing/deinitializing the account until distribution clears the pending balance. This matches the "temporary grief of funds / delayed withdrawal" impact class that the referenced report was scored as Medium severity for, rather than a critical fund-theft or consensus-divergence bug.

### Likelihood Explanation
Likelihood is bounded by feature-gating: `DepositDelegatorRewards` requires `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing` features to all be active (SIMD-0291, SIMD-0232, SIMD-0123), and the vote account must already be VoteStateV4: [6](#0-5) 
Once those features are active on mainnet, the call requires no special privilege beyond an ordinary funded, signed transaction, making the griefing trivially and repeatedly triggerable by any user against any V4 vote account.

### Recommendation
Do not allow full-withdraw blocking to be triggered by third parties who have no relationship to the withdrawer's intended action. Options include: (1) requiring the authorized withdrawer's consent/co-signature for `DepositDelegatorRewards` deposits, (2) allowing partial/full withdrawal to proceed by carving out only the strictly-reserved `pending_delegator_rewards` amount rather than blocking the entire close, or (3) providing a withdrawer-triggerable path to force-drain `pending_delegator_rewards` (e.g., forfeiting it to the incinerator or immediately distributing it) before a full close, so a third party cannot indefinitely gate account closure by injecting dust deposits.

### Proof of Concept
Given a VoteStateV4 account with `pending_delegator_rewards == 0` that is otherwise eligible for a full-close withdrawal:
1. Attacker (unrelated, unprivileged signer) submits `DepositDelegatorRewards { deposit: 1 }` naming the victim's vote account and themself as the funding source, per `deposit_delegator_rewards`: [7](#0-6) 
2. This sets `pending_delegator_rewards = 1` on the victim vote account.
3. Victim's authorized withdrawer submits `Withdraw` for the full account balance; this now fails with `InstructionError::InsufficientFunds` because pending rewards are nonzero, exactly as validated by the existing unit test: [3](#0-2) 
4. The victim must wait until the next block-revenue-sharing distribution cycle clears `pending_delegator_rewards` before retrying the full close; the attacker can repeat step 1 to re-arm the block indefinitely at negligible cost.

### Citations

**File:** programs/vote/src/vote_processor.rs (L409-426)
```rust
        VoteInstruction::DepositDelegatorRewards { deposit } => {
            // SIMD-0123: Deposit delegator rewards.
            // Requires:
            // * SIMD-0185: Vote State V4
            // * SIMD-0291: Commission in Basis Points
            // * SIMD-0232: Custom Commission Collector
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points
                || !feature_set.custom_commission_collector
                || !feature_set.block_revenue_sharing
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(2)?;
            drop(me);
            vote_state::deposit_delegator_rewards(invoke_context, 0, 1, deposit, &signers)
        }
```

**File:** programs/vote/src/vote_state/mod.rs (L935-988)
```rust
/// Deposit delegator rewards into a vote account (SIMD-0123).
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L5898-5921)
```rust
        // Full close blocked when pending > 0.
        {
            let (handler, account) = make_v4_account_with_pending(&vote_pubkey, 1, 1_000_000);
            let withdrawer = *handler.authorized_withdrawer();
            let signers: HashSet<Pubkey> = [withdrawer].into_iter().collect();
            let lamports = rent.minimum_balance(VoteStateV4::size_of()) + 1_000_000;
            let tx = setup_withdraw_context(vote_pubkey, account);
            let ix = tx.get_next_instruction_context().unwrap();

            assert_eq!(
                withdraw(
                    &ix,
                    0,
                    VoteStateTargetVersion::V4,
                    lamports,
                    1,
                    &signers,
                    &rent,
                    &clock
                ),
                Err(InstructionError::InsufficientFunds)
            );
        }

```

**File:** programs/vote/src/vote_state/mod.rs (L5922-5943)
```rust
        // Full close succeeds when pending = 0.
        {
            let (handler, account) = make_v4_account_with_pending(&vote_pubkey, 0, 100);
            let withdrawer = *handler.authorized_withdrawer();
            let signers: HashSet<Pubkey> = [withdrawer].into_iter().collect();
            let lamports = rent.minimum_balance(VoteStateV4::size_of()) + 100;
            let tx = setup_withdraw_context(vote_pubkey, account);
            let ix = tx.get_next_instruction_context().unwrap();

            withdraw(
                &ix,
                0,
                VoteStateTargetVersion::V4,
                lamports,
                1,
                &signers,
                &rent,
                &clock,
            )
            .unwrap();
        }
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L1-23)
```rust
use {
    super::Bank,
    crate::{
        bank::CollectorFeeDetails,
        inflation_rewards::{MAX_BPS, MAX_BPS_U128},
        reward_info::RewardInfo,
    },
    agave_reserved_account_keys::ReservedAccountKeys,
    log::debug,
    solana_account::{AccountSharedData, ReadableAccount, WritableAccount},
    solana_pubkey::Pubkey,
    solana_rent::Rent,
    solana_reward_info::RewardType,
    solana_runtime_transaction::{
        transaction_meta::TransactionConfiguration, transaction_with_meta::TransactionWithMeta,
    },
    solana_sdk_ids::incinerator,
    solana_svm::rent_calculator::check_static_account_rent_state_transition,
    solana_system_interface::program as system_program,
    solana_vote::vote_state_view_mut::VoteStateViewMut,
    std::{result::Result, sync::atomic::Ordering::Relaxed},
    thiserror::Error,
};
```
