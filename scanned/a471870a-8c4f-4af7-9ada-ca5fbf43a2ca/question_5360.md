# Q5360: get-account-scaled-debt via borrow: reuse one price and index snapshot across a batch that mut

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `amount` reach `get-account-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:307) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it reads one scaled debt row, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:307` -> `get-account-scaled-debt`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-account-scaled-debt` reads one scaled debt row. Reach it through `borrow` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with `amount` varied, and assert that the value `get-account-scaled-debt` returns is identical in both runs; a divergence confirms the finding.
