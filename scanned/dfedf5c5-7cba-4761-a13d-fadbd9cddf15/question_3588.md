# Q3588: accrue-user-debts via deposit: leave the accrual clock stale so a later interval double-c

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `amount` reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it folds accrual over the position's debt list only, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `deposit` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `deposit` in simnet and assert `accrue-user-debts` never returns a value that breaks the invariant.
