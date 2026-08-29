# Q1416: vault-accrue via redeem: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `min-out` reach `vault-accrue` (mainnet/contracts/market/v0-4-market.clar:189) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it dispatches accrual to one of six vaults by asset id, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:189` -> `vault-accrue`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `vault-accrue` dispatches accrual to one of six vaults by asset id. Reach it through `redeem` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `min-out` across its boundary values through `redeem` in simnet and assert `vault-accrue` never returns a value that breaks the invariant.
