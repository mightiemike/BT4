# Q0740: get-account-scaled-debt via repay: pass a health check and then change, in the same transacti

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls whether the repaid asset is in the accrued debt list reach `get-account-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:307) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it reads one scaled debt row, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:307` -> `get-account-scaled-debt`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: whether the repaid asset is in the accrued debt list
- Exploit idea: `get-account-scaled-debt` reads one scaled debt row. Reach it through `repay` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `repay` twice with whether the repaid asset is in the accrued debt list varied, and assert that the value `get-account-scaled-debt` returns is identical in both runs; a divergence confirms the finding.
