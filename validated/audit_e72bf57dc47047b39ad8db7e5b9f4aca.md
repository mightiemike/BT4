## Title
Excess validator transaction fees silently destroyed (not credited, not carried over) when capped by `TransactionFeeConfig` limit - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

## Summary
`update_stake_pool` reads a validator's entire accumulated per-epoch fee from `PendingTransactionFee.pending_fee_by_validator`, removes that entry outright, and then *caps* the value used for minting/crediting at `max_fee_octa_allowed_per_epoch_per_pool`. Any amount above the cap is neither minted to the pool, nor re-added to the map for the next epoch, nor burned/accounted for anywhere — it is simply dropped. This is the same "excess funds ignored" class of bug as the external report: an accumulated value is capped at a threshold and the remainder vanishes instead of rolling forward.

## Finding Description
In `update_stake_pool`: [1](#0-0) 

```move
let fee_limit =
    if (exists<TransactionFeeConfig>(@aptos_framework)) {
        let TransactionFeeConfig::V0 { max_fee_octa_allowed_per_epoch_per_pool } =
            borrow_global<TransactionFeeConfig>(@aptos_framework);
        *max_fee_octa_allowed_per_epoch_per_pool
    } else {
        MAX_U64 as u64
    };

if (exists<PendingTransactionFee>(@aptos_framework)) {
    let pending_fee_by_validator =
        &mut borrow_global_mut<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
    if (pending_fee_by_validator.contains(&validator_index)) {
        let fee_octa = pending_fee_by_validator.remove(&validator_index).read();
        if (fee_octa > fee_limit) {
            fee_octa = fee_limit;
        };
        ...
        fee_active = fee_octa - fee_pending_inactive;
    }
};
```

`pending_fee_by_validator.remove(&validator_index)` unconditionally pulls the *entire* accumulated aggregator value for that validator out of the map (this is the "chest" analog to the ChestManager balance). The code then caps the local `fee_octa` at `fee_limit` for the purposes of minting into `stake_pool.active` / `stake_pool.pending_inactive`, but the removed entry is gone — there is no code path that re-adds `fee_octa - fee_limit` back to `pending_fee_by_validator` for next epoch, and no code path that mints/burns/tracks the excess elsewhere.

At the next epoch boundary, `pending_fee_by_validator` is unconditionally reset to fresh, zeroed aggregators for every active validator: [2](#0-1) 

```move
&mut borrow_global_mut<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
assert!(pending_fee_by_validator.is_empty(), error::internal(ETRANSACTION_FEE_NOT_FULLY_DISTRIBUTED));
validator_set.active_validators.for_each_ref(|v| pending_fee_by_validator.add(
    v.config.validator_index, aggregator_v2::create_unbounded_aggregator<u64>()
));
```

confirming that any capped remainder had no chance to survive into the next epoch — it is permanently lost from the ledger's accounting the moment `remove()` executes.

The existing unit test even documents this behavior without flagging it as a bug: with `max_fee_octa_allowed_per_epoch_per_pool = 20` and an accumulated fee of `222` for `validator_1`, only `20` is minted/credited and the `DistributeTransactionFee` event reports `fee_amount: 20` — the other `202` octas of network-collected gas fees disappear. [3](#0-2) 

## Impact Explanation
This function runs unconditionally at every epoch transition (`on_new_epoch` → `update_stake_pool`) whenever `TransactionFeeConfig` is set with a nonzero limit and `is_distribute_transaction_fee_enabled()`/fee-distribution features are active. The bug causes committed on-chain economic state (validator stake pool balances) to permanently and silently diverge from the correct/intended VM result: fees that were actually collected from users (already burned from the payer's balance and recorded in `PendingTransactionFee` via `record_fee`, which is invoked by the VM based on real `FeeStatement`s) never reach any account, are not re-queued, and are not accounted as burned. This is a real loss of value from the total-supply/accounting invariant of the chain's native token, occurring deterministically for every validator whose per-epoch fee total exceeds the configured cap — a durable, ledger-affecting divergence rather than a display/event-only issue.

## Likelihood Explanation
The trigger only requires:
1. Governance to have set `TransactionFeeConfig` with a `max_fee_octa_allowed_per_epoch_per_pool` limit (an intended feature, not a privileged-attacker scenario), and
2. Any validator to accumulate fees above that limit within an epoch (entirely plausible for high-throughput/high-fee validators or a low configured limit).

No malicious actor or governance is required to *exploit* this — it triggers automatically as an unintended side effect of normal fee-limiting configuration, which is why this qualifies as an unprivileged root cause rather than an admin-assumption issue: the admin's intended semantics (rate-limit fee distribution per pool per epoch) do not include "destroy the excess," yet that is what the code silently does.

## Recommendation
Do not discard the excess when capping. Either:
- Re-insert `fee_octa - fee_limit` back into the validator's aggregator entry in `pending_fee_by_validator` so it rolls into next epoch's distribution (mirroring the recommended fix in the external report — carry the excess forward instead of dropping it), or
- If capping is intended to permanently forfeit the excess, explicitly and visibly account for it (e.g., route it to a treasury/burn address with an event), rather than letting it vanish with no trace.
Additionally, `refresh_validator_set_in_place`'s invariant assertion (`pending_fee_by_validator.is_empty()`) should be revisited if amounts are meant to carry over across epochs.

## Proof of Concept
This is directly demonstrated by the existing repository test `test_transaction_fee_limit`: [4](#0-3) 

Steps:
1. `record_fee` accumulates `11` octas for `validator_0` and `222` octas for `validator_1` in `PendingTransactionFee`.
2. Governance sets `TransactionFeeConfig::V0 { max_fee_octa_allowed_per_epoch_per_pool: 20 }`.
3. `end_epoch()` invokes `update_stake_pool`, which removes the full `222` for `validator_1` from the map, caps it to `20`, mints/credits only `20` to the stake pool, and emits `DistributeTransactionFee { pool_address: address_1, fee_amount: 20 }`.
4. The `202` octa difference is unaccounted for anywhere in subsequent state — the pending fee map is reset to zero for all validators next epoch (`assert!(pending_fee_by_validator.is_empty())` followed by fresh zero aggregators), so the value is permanently lost from circulation without any burn record.

Note: I did not have access to the file that defines `big_ordered_map`/`aggregator_v2::add` semantics or confirm whether any other module elsewhere re-credits this excess (e.g., a treasury sweep); based on the code paths available in the index, no such compensating mechanism was found in `stake.move`. If such a path exists elsewhere in the framework, it would need to be checked to rule out this finding — I recommend verifying with a full-repository search/session before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1471-1478)
```text
                &mut borrow_global_mut<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
            assert!(
                pending_fee_by_validator.is_empty(),
                error::internal(ETRANSACTION_FEE_NOT_FULLY_DISTRIBUTED)
            );
            validator_set.active_validators.for_each_ref(|v| pending_fee_by_validator.add(
                v.config.validator_index, aggregator_v2::create_unbounded_aggregator<u64>()
            ));
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1866-1894)
```text
        let fee_pending_inactive = 0;
        let fee_active = 0;
        let fee_limit =
            if (exists<TransactionFeeConfig>(@aptos_framework)) {
                let TransactionFeeConfig::V0 { max_fee_octa_allowed_per_epoch_per_pool } =
                    borrow_global<TransactionFeeConfig>(@aptos_framework);
                *max_fee_octa_allowed_per_epoch_per_pool
            } else {
                MAX_U64 as u64
            };

        if (exists<PendingTransactionFee>(@aptos_framework)) {
            let pending_fee_by_validator =
                &mut borrow_global_mut<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
            if (pending_fee_by_validator.contains(&validator_index)) {
                let fee_octa = pending_fee_by_validator.remove(&validator_index).read();
                if (fee_octa > fee_limit) {
                    fee_octa = fee_limit;
                };
                let stake_active = (coin::value(&stake_pool.active) as u128);
                let stake_pending_inactive =
                    (coin::value(&stake_pool.pending_inactive) as u128);
                fee_pending_inactive =
                    (
                        ((fee_octa as u128) * stake_pending_inactive
                            / (stake_active + stake_pending_inactive)) as u64
                    );
                fee_active = fee_octa - fee_pending_inactive;
            }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L3913-3958)
```text
        record_fee(vm, vector[], vector[]);
        record_fee(
            vm,
            vector[get_validator_index(address_0)],
            vector[1]
        );
        record_fee(
            vm,
            vector[get_validator_index(address_1)],
            vector[2]
        );
        record_fee(
            vm,
            vector[get_validator_index(address_0), get_validator_index(address_1)],
            vector[10, 220]
        );

        {
            let fee_table =
                &borrow_global<PendingTransactionFee>(@aptos_framework).pending_fee_by_validator;
            assert!(
                fee_table.borrow(&get_validator_index(address_0)).read() == 11,
                0
            );
            assert!(
                fee_table.borrow(&get_validator_index(address_1)).read() == 222,
                0
            );
            let config = TransactionFeeConfig::V0 {
                max_fee_octa_allowed_per_epoch_per_pool: 20
            };
            set_transaction_fee_config(aptos_framework, config);
            end_epoch();

            assert!(
                event::was_event_emitted(
                    &DistributeTransactionFee { pool_address: address_0, fee_amount: 11 }
                ),
                0
            );
            assert!(
                event::was_event_emitted(
                    &DistributeTransactionFee { pool_address: address_1, fee_amount: 20 }
                ),
                0
            );
```
