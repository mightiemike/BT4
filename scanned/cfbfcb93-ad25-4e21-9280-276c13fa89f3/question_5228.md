# Q5228: refresh via collateral-add: pass a health check and then change, in the same transacti

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the `ft` trait principal reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `collateral-add` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the `ft` trait principal varied, and assert that the value `refresh` returns is identical in both runs; a divergence confirms the finding.
