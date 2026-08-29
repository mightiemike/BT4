# Q4130: total-debt via liquidate-redeem: leave the accrual clock stale so a later interval double-c

## Question
Entering through `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) while controlling the redemption receiver, can an unprivileged attacker make `total-debt` (mainnet/contracts/vault/v0-vault-stx.clar:328) leave the accrual clock stale so a later interval double-counts elapsed time? `total-debt` computes cumulative debt from `principal-scaled` and `index`, so the invariant that every second of elapsed time is charged exactly once, to one index, in one direction would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:328` -> `total-debt`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `total-debt` computes cumulative debt from `principal-scaled` and `index`. Reach it through `liquidate-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the redemption receiver varied, and assert that the value `total-debt` returns is identical in both runs; a divergence confirms the finding.
