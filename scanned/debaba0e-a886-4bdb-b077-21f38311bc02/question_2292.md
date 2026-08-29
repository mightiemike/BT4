# Q2292: calc-liq-factor-bound via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `debt-amount` reach `calc-liq-factor-bound` (mainnet/contracts/market/v0-4-market.clar:718) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it scales the penalty between a min and a max, capped at the max, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:718` -> `calc-liq-factor-bound`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `calc-liq-factor-bound` scales the penalty between a min and a max, capped at the max. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `debt-amount` across its boundary values through `liquidate` in simnet and assert `calc-liq-factor-bound` never returns a value that breaks the invariant.
