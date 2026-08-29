# Q1057: active via collateral-remove: reuse one price and index snapshot across a batch that mut

## Question
Can an unprivileged attacker entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), controlling `receiver`, including a contract principal, drive `active` (mainnet/contracts/registry/v0-egroup.clar:238) — which lists candidate bucket masks at or above a population — to reuse one price and index snapshot across a batch that mutates state between entries, breaking the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives, and cause direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:238` -> `active`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `active` lists candidate bucket masks at or above a population. Reach it through `collateral-remove` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `collateral-remove` with `receiver`, including a contract principal, then read `active` state before and after in the same block and assert the two sides of the invariant are equal.
