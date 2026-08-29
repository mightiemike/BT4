# Q0846: insert via borrow: absorb a sub-step failure into a fold flag and proceed on 

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling the order of accrual versus price resolution inside the let, can an unprivileged attacker make `insert` (mainnet/contracts/market/v0-market-vault.clar:159) absorb a sub-step failure into a fold flag and proceed on partial state? `insert` rewrites the whole registry entry for a user id, so the invariant that a value read from `index-cache` describes the vault as it is at the moment of use would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the order of accrual versus price resolution inside the let
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `borrow` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the order of accrual versus price resolution inside the let across its boundary values through `borrow` in simnet and assert `insert` never returns a value that breaks the invariant.
