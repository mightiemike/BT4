# Q5733: population via collateral-remove: absorb a sub-step failure into a fold flag and proceed on 

## Question
Can an unprivileged attacker entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), controlling `receiver`, including a contract principal, drive `population` (mainnet/contracts/registry/v0-egroup.clar:81) — which counts set bits to order the bucket search — to absorb a sub-step failure into a fold flag and proceed on partial state, breaking the invariant that every second of elapsed time is charged exactly once, to one index, in one direction, and cause permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:81` -> `population`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `population` counts set bits to order the bucket search. Reach it through `collateral-remove` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `population` touches, run `collateral-remove` with `receiver`, including a contract principal, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
