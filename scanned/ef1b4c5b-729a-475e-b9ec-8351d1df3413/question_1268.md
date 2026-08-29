# Q1268: get-bitmap via borrow: pass a health check and then change, in the same transacti

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `ft` trait principal reach `get-bitmap` (mainnet/contracts/registry/v0-assets.clar:145) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it returns the global enabled bitmap that every position read filters on, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:145` -> `get-bitmap`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `get-bitmap` returns the global enabled bitmap that every position read filters on. Reach it through `borrow` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `ft` trait principal varied, and assert that the value `get-bitmap` returns is identical in both runs; a divergence confirms the finding.
