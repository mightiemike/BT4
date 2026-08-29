# Q4044: interpolate-rate via liquidate: strand value on the market contract when a later step of a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it interpolates between packed u16 curve points, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `interpolate-rate` never returns a value that breaks the invariant.
