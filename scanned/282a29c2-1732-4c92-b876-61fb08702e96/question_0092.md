# Q0092: add-user-scaled-debt via borrow: consume a cache entry after the vault it describes has alr

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `add-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:237) in a state where it consume a cache entry after the vault it describes has already moved? Given that it adds to the scaled debt row with a graceful u0 default, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:237` -> `add-user-scaled-debt`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `add-user-scaled-debt` adds to the scaled debt row with a graceful u0 default. Reach it through `borrow` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with `receiver`, including a contract principal varied, and assert that the value `add-user-scaled-debt` returns is identical in both runs; a divergence confirms the finding.
