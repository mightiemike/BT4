# Q4824: receive-tokens via liquidate-redeem: bind a balance before an accrual and assert against it aft

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the borrower targeted reach `receive-tokens` (mainnet/contracts/market/v0-market-vault.clar:256) in a state where it bind a balance before an accrual and assert against it after? Given that it pulls an asset from a named account, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:256` -> `receive-tokens`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `receive-tokens` pulls an asset from a named account. Reach it through `liquidate-redeem` and bind a balance before an accrual and assert against it after.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the borrower targeted across its boundary values through `liquidate-redeem` in simnet and assert `receive-tokens` never returns a value that breaks the invariant.
