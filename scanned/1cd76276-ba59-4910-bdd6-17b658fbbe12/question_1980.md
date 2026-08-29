# Q1980: get-asset-value via borrow: strand value on the market contract when a later step of a

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `receiver`, including a contract principal reach `get-asset-value` (mainnet/contracts/market/v0-4-market.clar:679) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:679` -> `get-asset-value`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `get-asset-value` resolves a fresh price for a single asset and normalizes with a caller-supplied rounding direction. Reach it through `borrow` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `receiver`, including a contract principal across its boundary values through `borrow` in simnet and assert `get-asset-value` never returns a value that breaks the invariant.
