# Q2703: calc-cumulative-debt via supply-collateral-add: leave the accrual clock stale so a later interval double-c

## Question
`calc-cumulative-debt` (mainnet/contracts/vault/v0-vault-stx.clar:180) multiplies scaled principal by an index. Can an unprivileged caller of `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175), by choosing the position state the final collateral-add is validated against, use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that a failed sub-step aborts the transaction or is explicitly compensated and producing permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:180` -> `calc-cumulative-debt`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the position state the final collateral-add is validated against
- Exploit idea: `calc-cumulative-debt` multiplies scaled principal by an index. Reach it through `supply-collateral-add` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `calc-cumulative-debt` touches, run `supply-collateral-add` with the position state the final collateral-add is validated against, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
