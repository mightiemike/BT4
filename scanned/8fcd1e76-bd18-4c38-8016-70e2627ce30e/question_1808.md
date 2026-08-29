# Q1808: send-tokens via collateral-add: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the three `price-feeds` buffers and their order reach `send-tokens` (mainnet/contracts/market/v0-market-vault.clar:259) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it pushes an asset to a caller-chosen recipient principal, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:259` -> `send-tokens`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the three `price-feeds` buffers and their order
- Exploit idea: `send-tokens` pushes an asset to a caller-chosen recipient principal. Reach it through `collateral-add` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the three `price-feeds` buffers and their order varied, and assert that the value `send-tokens` returns is identical in both runs; a divergence confirms the finding.
