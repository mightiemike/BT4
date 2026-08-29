# Q5832: resolve-pyth via liquidate: strand value on the market contract when a later step of a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls which collateral and debt asset pair is targeted reach `resolve-pyth` (mainnet/contracts/market/v0-4-market.clar:312) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it reads the Pyth storage record for a 32-byte ident, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:312` -> `resolve-pyth`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `resolve-pyth` reads the Pyth storage record for a 32-byte ident. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz which collateral and debt asset pair is targeted across its boundary values through `liquidate` in simnet and assert `resolve-pyth` never returns a value that breaks the invariant.
