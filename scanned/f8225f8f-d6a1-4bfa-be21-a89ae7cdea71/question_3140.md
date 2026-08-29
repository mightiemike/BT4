# Q3140: collateral-remove via collateral-remove: leave the accrual clock stale so a later interval double-c

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the `price-feeds` buffers reach `collateral-remove` (mainnet/contracts/market/v0-market-vault.clar:406) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it decrements the map and writes the entry before `send-tokens` executes, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:406` -> `collateral-remove`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `collateral-remove` decrements the map and writes the entry before `send-tokens` executes. Reach it through `collateral-remove` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with the `price-feeds` buffers varied, and assert that the value `collateral-remove` returns is identical in both runs; a divergence confirms the finding.
