# Q3600: calc-index-next via collateral-remove: strand value on the market contract when a later step of a

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the `ft` trait principal reach `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it applies a multiplier to the current index, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `collateral-remove` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the `ft` trait principal across its boundary values through `collateral-remove` in simnet and assert `calc-index-next` never returns a value that breaks the invariant.
