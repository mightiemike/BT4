[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** vote/src/vote_account.rs (L53-60)
```rust
#[cfg_attr(feature = "frozen-abi", derive(AbiExample))]
#[derive(Debug)]
struct VoteAccountInner {
    account: AccountSharedData,
    vote_state_view: VoteStateView,
}

pub type VoteAccountsHashMap = HashMap<Pubkey, (/*stake:*/ u64, VoteAccount)>;
```

**File:** runtime/src/bank/fee_distribution.rs (L147-177)
```rust
        // Per SIMD-0232: the commission collector address should be fetched
        // from the state of the vote account at the beginning of the previous
        // epoch. This is the vote account state used to build the leader
        // schedule for the current epoch, which *DOES NOT* correspond to
        // `Bank::current_epoch_stakes()`.
        let feature_snapshot = self.feature_set.snapshot();
        let (collector_id, commission_bps) = if feature_snapshot.custom_commission_collector {
            let vote_account = self
                .epoch_stakes
                .get(&self.epoch)
                .and_then(|stakes| {
                    stakes
                        .stakes()
                        .vote_accounts()
                        .get(&self.leader.vote_address)
                })
                .expect("The vote account for the leader must exist");
            (
                // Protection in case the leader is on a vote state without a
                // collector id, which can happen if a dormant pre-v4 vote state
                // accrues stake.
                vote_account
                    .vote_state_view()
                    .block_revenue_collector()
                    .unwrap_or(&self.leader.id),
                // For pre-v4 vote states, defaults to the max of 10_000 bps
                vote_account.vote_state_view().block_revenue_commission(),
            )
        } else {
            (&self.leader.id, MAX_BPS)
        };
```

**File:** runtime/src/bank/fee_distribution.rs (L296-326)
```rust
    // Deposits delegator fees into the specified vote account and increments
    // pending delegator rewards. If successful, returns the new balance of that
    // account
    fn deposit_delegator_fees(
        &self,
        vote_address: &Pubkey,
        fees: u64,
    ) -> Result<u64, DepositFeeError> {
        let mut account = self
            .get_account_with_fixed_root_no_cache(vote_address)
            .ok_or(DepositFeeError::InvalidVoteAccount)?;

        if *account.owner() != solana_sdk_ids::vote::id() {
            return Err(DepositFeeError::InvalidVoteAccount);
        }

        let account_data = account.data_as_mut_slice();
        let mut vote_state = VoteStateViewMut::new_v4(account_data)
            .map_err(|_| DepositFeeError::InvalidVoteAccount)?;

        vote_state
            .increment_pending_delegator_rewards_checked(fees)
            .ok_or(DepositFeeError::LamportOverflow)?;

        account
            .checked_add_lamports(fees)
            .map_err(|_| DepositFeeError::LamportOverflow)?;

        self.store_account(vote_address, &account);
        Ok(account.lamports())
    }
```
