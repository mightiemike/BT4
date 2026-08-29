# Q1022: get-egroup via borrow: reuse one price and index snapshot across a batch that mut

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling the `ft` trait principal, can an unprivileged attacker make `get-egroup` (mainnet/contracts/market/v0-4-market.clar:460) reuse one price and index snapshot across a batch that mutates state between entries? `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path, so the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives would fail, yielding protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:460` -> `get-egroup`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path. Reach it through `borrow` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `ft` trait principal varied, and assert that the value `get-egroup` returns is identical in both runs; a divergence confirms the finding.
