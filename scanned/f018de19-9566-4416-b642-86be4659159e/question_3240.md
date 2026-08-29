# Q3240: accrue-user-debts via redeem: reuse one price and index snapshot across a batch that mut

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `recipient` reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it folds accrual over the position's debt list only, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `recipient` across its boundary values through `redeem` in simnet and assert `accrue-user-debts` never returns a value that breaks the invariant.
