### Title
Unstake-to-zero proposals are silently overridden by reward accrual, keeping validators locked against their request - ([File: chain/epoch-manager/src/validator_selection.rs])

### Summary
`apply_epoch_update_to_proposals` unconditionally adds a validator's earned reward to its next-epoch proposed stake, even when the validator explicitly submitted a proposal with `stake == 0` (i.e., a full-unstake request). The documented protocol rule requires the reward to be dropped in that case, exactly analogous to the reported Solidity bug where a self-referential delegate change should skip the balance adjustment rather than blindly applying it.

### Finding Description
The project's own specification states the rule for combining a validator's proposal with its earned reward:

> "considered stake of the proposal is `0 if proposal.stake == 0 else proposal.stake + reward[proposal.account_id]`" [1](#0-0) 

However, the implementation in `apply_epoch_update_to_proposals` never checks for the zero-stake case before adding the reward:

```
let p = proposals_by_account.entry(account_id).or_insert(r);
if let Some(reward) = validator_reward.get(p.account_id()) {
    *p.stake_mut() = p.stake().checked_add(*reward).unwrap();
}
stake_change.insert(p.account_id().clone(), p.stake());
``` [2](#0-1) 

When a currently-validating account submits a `Stake` action with `stake = 0` intending to fully unstake, that proposal is inserted into `proposals_by_account` in the first loop with the zero value: [3](#0-2)  Because this account was also a validator in `prev_epoch_info`, the second loop's `entry(...).or_insert(r)` finds the existing (zero-stake) proposal entry rather than inserting the fallback `r`, and then unconditionally adds `reward` to it — exactly the missing self/previous-value check pattern described in the report (there, the code failed to skip an adjustment when the "previous" and "new" targets were the same entity; here, the code fails to skip an adjustment when the proposal is a self-canceling zero-stake request).

This resulting non-zero `p.stake()` is then fed into `select_validators_from_proposals` for chunk/block/chunk-validator seat selection [4](#0-3) , and is recorded into `stake_change`/`EpochInfo` as the account's stake for the next epoch, instead of the `0` the spec mandates.

### Impact Explanation
An account that explicitly proposes to fully unstake (a routine, unprivileged staking transaction) can be re-selected as a validator for the next epoch with its earned reward as its new stake, and/or have its `locked` balance updating logic in `Runtime::update_validator_accounts` compute a smaller `return_stake` than the account is entitled to, since `max(max_of_stakes, last_proposal)` will reflect an inflated stake value coming from `stake_change`/epoch info rather than the account's true zero intent. This causes an invalid state transition relative to the documented protocol rule (funds that should have been returned/unlocked remain locked, or the account remains an active validator against its own unstake request) — a concrete instance of frozen/misaccounted stake reachable purely by an unprivileged validator submitting a normal `Stake(0)` transaction.

### Likelihood Explanation
High reachability: any validator can trigger this simply by submitting a `Stake` action with amount `0` (the standard way to fully unstake) in an epoch where they also earned any non-zero reward — a very common scenario since rewards accrue every epoch a validator is online. No special privileges, timing races, or malicious peers are required.

### Recommendation
Mirror the fix pattern from the reported bug (guard the adjustment with an equality/zero check before applying it): in the second loop of `apply_epoch_update_to_proposals`, only add the reward when the entry did not originate from an explicit zero-stake proposal, e.g.:
```rust
let p = proposals_by_account.entry(account_id).or_insert(r);
if p.stake() > Balance::ZERO {
    if let Some(reward) = validator_reward.get(p.account_id()) {
        *p.stake_mut() = p.stake().checked_add(*reward).unwrap();
    }
}
stake_change.insert(p.account_id().clone(), p.stake());
```
This restores the documented `0 if proposal.stake == 0 else proposal.stake + reward` semantics.

### Proof of Concept
1. Account `V` is a validator in epoch `T` with stake `S` and earns `reward R > 0` for good performance in `T` (per `RewardCalculator::calculate_reward`, `chain/epoch-manager/src/reward_calculator.rs:51`).
2. In epoch `T`, `V` submits a `Stake` action with `stake = 0`, intending to fully unstake for epoch `T+2`.
3. During `proposals_to_epoch_info` → `apply_epoch_update_to_proposals`, `V`'s proposal (`stake = 0`) is inserted in the first loop; in the second loop, since `V` was a prior validator, `entry(...).or_insert(r)` retrieves the existing zero-stake proposal, and the code unconditionally executes `p.stake_mut() = 0.checked_add(R) = R`.
4. `V`'s resulting proposal stake is `R` (not `0`), so `stake_change[V] = R` and `V` is fed into validator selection with stake `R` instead of being dropped — contradicting `V`'s explicit unstake request and the documented protocol spec.

### Citations

**File:** docs/Economics/Economics.md (L126-131)
```markdown
At the end of every epoch `T`, next algorithm gets executed to determine validators for epoch `T + 2`:

1. For every chunk/block producer in `epoch[T]` determine `num_blocks_produced`, `num_chunks_produced` based on what they produced during the epoch.
2. Remove validators, for whom `num_blocks_produced < num_blocks_expected * BLOCK_PRODUCER_KICKOUT_THRESHOLD` or `num_chunks_produced < num_chunks_expected * CHUNK_PRODUCER_KICKOUT_THRESHOLD`.
3. Collect chunk-only and block producer `proposals`, if validator was also a validator in `epoch[T]`, considered stake of the proposal is `0 if proposal.stake == 0 else proposal.stake + reward[proposal.account_id]`.
4. Use the chunk/block producer selection algorithms outlined in [Selecting Chunk and Block Producers](../ChainSpec/SelectingBlockProducers.md).
```

**File:** chain/epoch-manager/src/validator_selection.rs (L188-199)
```rust
    let proposals = apply_epoch_update_to_proposals(
        proposals,
        prev_epoch_info,
        &validator_reward,
        &validator_kickout,
        &mut stake_change,
    );

    // Select validators for the next epoch.
    // Returns unselected proposals, validator lists for all roles and stake
    // threshold to become a validator.
    let validator_roles = select_validators_from_proposals(epoch_config, proposals);
```

**File:** chain/epoch-manager/src/validator_selection.rs (L296-313)
```rust
) -> HashMap<AccountId, ValidatorStake> {
    let mut proposals_by_account = HashMap::new();
    for p in proposals {
        let account_id = p.account_id();
        if validator_kickout.contains_key(account_id) {
            let account_id = p.take_account_id();
            stake_change.insert(account_id, Balance::ZERO);
        } else if let Some(ValidatorKickoutReason::ProtocolVersionTooOld { .. }) =
            prev_epoch_info.validator_kickout().get(account_id)
        {
            // If the validator was kicked out because of an old protocol version in T-1,
            // it is not allowed back in T.
            continue;
        } else {
            stake_change.insert(account_id.clone(), p.stake());
            proposals_by_account.insert(account_id.clone(), p);
        }
    }
```

**File:** chain/epoch-manager/src/validator_selection.rs (L315-326)
```rust
    for r in prev_epoch_info.validators_iter() {
        let account_id = r.account_id().clone();
        if validator_kickout.contains_key(&account_id) {
            stake_change.insert(account_id, Balance::ZERO);
            continue;
        }
        let p = proposals_by_account.entry(account_id).or_insert(r);
        if let Some(reward) = validator_reward.get(p.account_id()) {
            *p.stake_mut() = p.stake().checked_add(*reward).unwrap();
        }
        stake_change.insert(p.account_id().clone(), p.stake());
    }
```
