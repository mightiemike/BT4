# Q0157: lookup via borrow: bind a balance before an accrual and assert against it aft

## Question
Can an unprivileged attacker entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), controlling `amount`, drive `lookup` (mainnet/contracts/registry/v0-assets.clar:139) — which returns the registry record, including the `decimals` captured once at registration — to bind a balance before an accrual and assert against it after, breaking the invariant that a failed sub-step aborts the transaction or is explicitly compensated, and cause direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:139` -> `lookup`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `lookup` returns the registry record, including the `decimals` captured once at registration. Reach it through `borrow` and bind a balance before an accrual and assert against it after.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `borrow` with `amount`, then read `lookup` state before and after in the same block and assert the two sides of the invariant are equal.
