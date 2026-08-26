# Q5492: epoch_rewards_calculation::store_commission_accounts_partitioned - commission account stored with wrong lamports (deactivating and reactivating the stake across)

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs, drive `epoch_rewards_calculation::store_commission_accounts_partitioned` to make store_commission_accounts_partitioned write a balance that does not match the computed commission, so that the invariant that stored commission balances equal the computed commissions is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `store_commission_accounts_partitioned`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, deactivating and reactivating the stake across two consecutive epochs
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make store_commission_accounts_partitioned write a balance that does not match the computed commission.
- Invariant to test: Stored commission balances equal the computed commissions.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
