# Q4804: zip via redeem: reuse one price and index snapshot across a batch that mut

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `recipient` reach `zip` (mainnet/contracts/vault/v0-vault-stx.clar:226) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it pairs the utilization and rate point lists element by element, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:226` -> `zip`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `zip` pairs the utilization and rate point lists element by element. Reach it through `redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `redeem` with `recipient`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
