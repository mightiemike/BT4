# Q1213: find-superset via collateral-add: absorb a sub-step failure into a fold flag and proceed on 

## Question
Can an unprivileged attacker entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020), controlling `amount`, drive `find-superset` (mainnet/contracts/registry/v0-egroup.clar:262) — which returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest — to absorb a sub-step failure into a fold flag and proceed on partial state, breaking the invariant that the state a safety check approved is the state the money movement executes against, and cause direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:262` -> `find-superset`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `find-superset` returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest. Reach it through `collateral-add` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `collateral-add` with `amount`, then read `find-superset` state before and after in the same block and assert the two sides of the invariant are equal.
