# Q0188: find-debt-scaled via collateral-remove: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls `receiver`, including a contract principal reach `find-debt-scaled` (mainnet/contracts/market/v0-4-market.clar:621) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it returns u0 for an absent asset, making a missing debt row indistinguishable from no debt, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:621` -> `find-debt-scaled`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `find-debt-scaled` returns u0 for an absent asset, making a missing debt row indistinguishable from no debt. Reach it through `collateral-remove` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with `receiver`, including a contract principal varied, and assert that the value `find-debt-scaled` returns is identical in both runs; a divergence confirms the finding.
