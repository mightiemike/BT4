# Q1520: calc-utilization via deposit: pass a health check and then change, in the same transacti

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `amount` reach `calc-utilization` (mainnet/contracts/vault/v0-vault-stx.clar:164) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it divides debt by available liquidity, which can exceed BPS when debt outruns assets, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:164` -> `calc-utilization`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `calc-utilization` divides debt by available liquidity, which can exceed BPS when debt outruns assets. Reach it through `deposit` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `deposit` twice with `amount` varied, and assert that the value `calc-utilization` returns is identical in both runs; a divergence confirms the finding.
