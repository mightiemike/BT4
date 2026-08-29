# Q1410: status via borrow: strand value on the market contract when a later step of a

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling `amount`, can an unprivileged attacker make `status` (mainnet/contracts/registry/v0-assets.clar:115) strand value on the market contract when a later step of a composite call fails? `status` derives `collateral` and `debt` flags from bit tests against whatever mask it was handed, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:115` -> `status`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `status` derives `collateral` and `debt` flags from bit tests against whatever mask it was handed. Reach it through `borrow` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `borrow` in simnet and assert `status` never returns a value that breaks the invariant.
