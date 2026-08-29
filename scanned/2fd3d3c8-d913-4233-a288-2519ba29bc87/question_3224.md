# Q3224: unwrap-status via borrow: leave the accrual clock stale so a later interval double-c

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the future mask produced by the new debt bit reach `unwrap-status` (mainnet/contracts/registry/v0-assets.clar:111) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it resolves `status` with `unwrap-panic`, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:111` -> `unwrap-status`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the future mask produced by the new debt bit
- Exploit idea: `unwrap-status` resolves `status` with `unwrap-panic`. Reach it through `borrow` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the future mask produced by the new debt bit varied, and assert that the value `unwrap-status` returns is identical in both runs; a divergence confirms the finding.
