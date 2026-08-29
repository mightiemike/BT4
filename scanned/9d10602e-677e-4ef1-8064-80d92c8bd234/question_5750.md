# Q5750: mask-update via borrow: leave the accrual clock stale so a later interval double-c

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling `amount`, can an unprivileged attacker make `mask-update` (mainnet/contracts/market/v0-market-vault.clar:94) leave the accrual clock stale so a later interval double-counts elapsed time? `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:94` -> `mask-update`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero. Reach it through `borrow` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with `amount` varied, and assert that the value `mask-update` returns is identical in both runs; a divergence confirms the finding.
