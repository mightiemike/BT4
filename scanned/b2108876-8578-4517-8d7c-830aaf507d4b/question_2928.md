# Q2928: interest-rate via borrow: bind a balance before an accrual and assert against it aft

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it bind a balance before an accrual and assert against it after? Given that it interpolates the packed curve at the current utilization, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `borrow` and bind a balance before an accrual and assert against it after.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `borrow` in simnet and assert `interest-rate` never returns a value that breaks the invariant.
