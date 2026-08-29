# Q0132: vault-accrue via repay: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls whether the repaid asset is in the accrued debt list reach `vault-accrue` (mainnet/contracts/market/v0-4-market.clar:189) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it dispatches accrual to one of six vaults by asset id, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:189` -> `vault-accrue`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: whether the repaid asset is in the accrued debt list
- Exploit idea: `vault-accrue` dispatches accrual to one of six vaults by asset id. Reach it through `repay` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz whether the repaid asset is in the accrued debt list across its boundary values through `repay` in simnet and assert `vault-accrue` never returns a value that breaks the invariant.
