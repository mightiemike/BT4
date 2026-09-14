## Analog Found: `minimum_stake()` can floor to zero, nullifying `InsufficientStake` validation on `StakeAction`

### Title
Integer-division truncation of `EpochManager::minimum_stake` can make the minimum-stake validation on `StakeAction` a no-op - (File: `chain/epoch-manager/src/lib.rs`)

### Summary
The Sherlock finding describes `getNewTargetedRate()` returning zero, which silently disables a downstream validation check that depends on it being non-zero. The same bug class exists in nearcore's staking-validation path: `EpochManager::minimum_stake` computes the minimum allowed stake via integer division of `seat_price` by `minimum_stake_divisor`, and that computed value gates the `InsufficientStake` check in `action_stake`. When `seat_price < minimum_stake_divisor`, integer division truncates the result to `0`, and the runtime check `stake.stake < minimum_stake` becomes trivially false for any non-zero stake, silently disabling the intended stake floor.

### Finding Description
`EpochManager::minimum_stake` computes: [1](#0-0) 

```
seat_price.checked_div(u128::from(stake_divisor)).unwrap()
```

`seat_price` comes from `find_threshold`, a binary search that can legitimately settle on a small value (as low as `1` yoctoNEAR) when total stake or seat count is small: [2](#0-1) 

`minimum_stake_divisor` is a genesis/epoch-config parameter (`u64`, default `10` on mainnet/testnet) that determines the divisor: [3](#0-2) 

When `seat_price < minimum_stake_divisor`, integer division truncates `minimum_stake` to `Balance::ZERO`. This value is consumed directly in `action_stake`, the code path executed for every user-submitted `StakeAction`: [4](#0-3) 

```rust
if stake.stake > Balance::ZERO {
    let minimum_stake = epoch_info_provider.minimum_stake(last_block_hash)?;
    if stake.stake < minimum_stake {
        result.result = Err(ActionErrorKind::InsufficientStake { ... }.into());
        return Ok(());
    }
}
result.validator_proposals.push(ValidatorStake::new(account_id.clone(), stake.public_key.clone(), stake.stake));
```

If `minimum_stake` is `0`, the comparison `stake.stake < minimum_stake` can never be true for any positive stake, so the `InsufficientStake` guard documented in the protocol spec — "the minimum stake required for staking is last seat price divided by [`minimum_stake_divisor`]" — is effectively disabled: [5](#0-4) 

This mirrors the Sherlock report exactly: a rate/threshold value derived from a live computation (`getNewTargetedRate` / `seat_price / divisor`) can legitimately evaluate to zero, and a subsequent validation check that assumes non-zero silently becomes a pass-through.

### Impact Explanation
When `minimum_stake` truncates to zero, any account can submit a `StakeAction` with an arbitrarily small non-zero stake (e.g. `1` yoctoNEAR) and have it accepted as a validator proposal, bypassing the intended economic floor for validator-seat eligibility: [6](#0-5) 

Because this affects the *validation-error* path (`InsufficientStake`), not the separate `min_stake_ratio` seat-selection logic, it undermines a documented security invariant (validators must clear a stake floor derived from the seat price) directly from an unprivileged, ordinary transaction signer. Depending on chain configuration (small total stake / small `num_block_producer_seats` relative to `minimum_stake_divisor`, or a chain deliberately configured with a larger divisor), this can let low-stake accounts flood validator proposals or otherwise defeat the intended Sybil-resistance of the minimum-stake gate — a state-transition/economic-invariant violation reachable purely through a signed `StakeAction` transaction.

### Likelihood Explanation
This requires `seat_price < minimum_stake_divisor` for the next epoch. On current mainnet/testnet configs (`minimum_stake_divisor = 10`), this is unlikely under normal high-stake conditions, but is easily reachable on smaller/custom networks (testnets, appchains, or any deployment with low total stake or a larger configured divisor), and is deterministic once that numeric condition holds — no adversarial timing or race is needed, just a `StakeAction` transaction submitted while the divisor exceeds the current seat price.

### Recommendation
In `EpochManager::minimum_stake` (`chain/epoch-manager/src/lib.rs`), guard against the truncation-to-zero case, e.g. round up the division (`ceil` instead of floor) or clamp the result to a minimum of `Balance::from_yoctonear(1)` when `seat_price > 0`, ensuring the `InsufficientStake` check in `runtime/runtime/src/actions.rs` remains meaningful regardless of the ratio between `seat_price` and `minimum_stake_divisor`.

### Proof of Concept
1. Configure (or let a live network evolve to) `seat_price < minimum_stake_divisor` for the upcoming epoch — e.g. genesis with `minimum_stake_divisor = 10` and total effective stake/seat count such that `find_threshold` returns a `seat_price` of e.g. `5`.
2. `EpochManager::minimum_stake` computes `5u128.checked_div(10).unwrap() == 0` [7](#0-6) .
3. Any account submits a `StakeAction { stake: 1, public_key }`. In `action_stake`, `stake.stake (1) > Balance::ZERO` is true, `minimum_stake` is `0`, so `stake.stake < minimum_stake` (`1 < 0`) is false — the `InsufficientStake` error is never raised, and a `ValidatorStake` proposal with `stake = 1` is pushed [8](#0-7) .
4. This proposal proceeds into validator-selection consideration despite trivially violating the documented minimum-stake floor for staking.

### Citations

**File:** chain/epoch-manager/src/lib.rs (L1956-1967)
```rust
    /// Get minimum stake allowed at current block. Attempts to stake with a lower stake will be
    /// rejected.
    pub fn minimum_stake(&self, prev_block_hash: &CryptoHash) -> Result<Balance, EpochError> {
        let next_epoch_id = self.get_next_epoch_id_from_prev_block(prev_block_hash)?;
        let (protocol_version, seat_price) = {
            let epoch_info = self.get_epoch_info(&next_epoch_id)?;
            (epoch_info.protocol_version(), epoch_info.seat_price())
        };
        let config = self.config.for_protocol_version(protocol_version);
        let stake_divisor = { config.minimum_stake_divisor };
        Ok(seat_price.checked_div(u128::from(stake_divisor)).unwrap())
    }
```

**File:** chain/epoch-manager/src/genesis.rs (L172-201)
```rust
pub(crate) fn find_threshold(
    stakes: &[Balance],
    num_seats: NumSeats,
) -> Result<Balance, EpochError> {
    let stake_sum: Balance =
        stakes.iter().fold(Balance::ZERO, |sum, item| sum.checked_add(*item).unwrap());
    let min_possible_stake = Balance::from_yoctonear(u128::from(num_seats));
    if stake_sum < min_possible_stake {
        return Err(EpochError::ThresholdError { stake_sum, num_seats });
    }
    let (mut left, mut right): (Balance, Balance) =
        (Balance::from_yoctonear(1), stake_sum.checked_add(Balance::from_yoctonear(1)).unwrap());
    'outer: loop {
        if left == right.checked_sub(Balance::from_yoctonear(1)).unwrap() {
            break Ok(left);
        }
        let mid = left.checked_add(right).unwrap().checked_div(2).unwrap();
        let mut current_sum = Balance::ZERO;
        for item in stakes {
            current_sum =
                current_sum.checked_add(item.checked_div(mid.as_yoctonear()).unwrap()).unwrap();
            let min_possible_stake = Balance::from_yoctonear(u128::from(num_seats));
            if current_sum >= min_possible_stake {
                left = mid;
                continue 'outer;
            }
        }
        right = mid;
    }
}
```

**File:** core/chain-configs/src/genesis_config.rs (L184-187)
```rust
    /// The minimum stake required for staking is last seat price divided by this number.
    #[serde(default = "default_minimum_stake_divisor")]
    #[default(10)]
    pub minimum_stake_divisor: u64,
```

**File:** runtime/runtime/src/actions.rs (L58-93)
```rust
pub(crate) fn action_stake(
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    stake: &StakeAction,
    last_block_hash: &CryptoHash,
    epoch_info_provider: &dyn EpochInfoProvider,
) -> Result<(), RuntimeError> {
    let increment = stake.stake.saturating_sub(account.locked());

    if let Some(new_balance) = account.amount().checked_sub(increment) {
        if account.locked().is_zero() && stake.stake.is_zero() {
            // if the account hasn't staked, it cannot unstake
            result.result =
                Err(ActionErrorKind::TriesToUnstake { account_id: account_id.clone() }.into());
            return Ok(());
        }

        if stake.stake > Balance::ZERO {
            let minimum_stake = epoch_info_provider.minimum_stake(last_block_hash)?;
            if stake.stake < minimum_stake {
                result.result = Err(ActionErrorKind::InsufficientStake {
                    account_id: account_id.clone(),
                    stake: stake.stake,
                    minimum_stake,
                }
                .into());
                return Ok(());
            }
        }

        result.validator_proposals.push(ValidatorStake::new(
            account_id.clone(),
            stake.public_key.clone(),
            stake.stake,
        ));
```

**File:** docs/RuntimeSpec/Actions.md (L186-198)
```markdown
- If the staked amount is below the minimum stake threshold, the following error will be returned:

```rust
InsufficientStake {
    account_id: AccountId,
    stake: Balance,
    minimum_stake: Balance,
}
```

The minimum stake is determined by `last_epoch_seat_price / minimum_stake_divisor` where `last_epoch_seat_price` is the
seat price determined at the end of last epoch and `minimum_stake_divisor` is a genesis config parameter and its current
value is 10.
```
