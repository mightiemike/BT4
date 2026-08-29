# Q2834: unwrap-status via liquidate: strand value on the market contract when a later step of a

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `collateral-receiver`, can an unprivileged attacker make `unwrap-status` (mainnet/contracts/registry/v0-assets.clar:111) strand value on the market contract when a later step of a composite call fails? `unwrap-status` resolves `status` with `unwrap-panic`, so the invariant that every second of elapsed time is charged exactly once, to one index, in one direction would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:111` -> `unwrap-status`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `unwrap-status` resolves `status` with `unwrap-panic`. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `unwrap-status` returns is identical in both runs; a divergence confirms the finding.
