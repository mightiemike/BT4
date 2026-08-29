# Q2708: vault-system-borrow via repay: strand value on the market contract when a later step of a

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `on-behalf-of`, naming any third-party principal reach `vault-system-borrow` (mainnet/contracts/market/v0-4-market.clar:198) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it routes a borrow to one of six vaults by asset id, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:198` -> `vault-system-borrow`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `on-behalf-of`, naming any third-party principal
- Exploit idea: `vault-system-borrow` routes a borrow to one of six vaults by asset id. Reach it through `repay` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `repay` twice with `on-behalf-of`, naming any third-party principal varied, and assert that the value `vault-system-borrow` returns is identical in both runs; a divergence confirms the finding.
