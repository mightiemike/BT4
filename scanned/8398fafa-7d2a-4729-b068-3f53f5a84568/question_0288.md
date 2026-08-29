# Q0288: zip via call-ststx-ratio: consume a cache entry after the vault it describes has alr

## Question
Does `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) let an unprivileged attacker who controls whether the ratio is fetched before or after other state changes in the block reach `zip` (mainnet/contracts/vault/v0-vault-stx.clar:226) in a state where it consume a cache entry after the vault it describes has already moved? Given that it pairs the utilization and rate point lists element by element, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:226` -> `zip`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: whether the ratio is fetched before or after other state changes in the block
- Exploit idea: `zip` pairs the utilization and rate point lists element by element. Reach it through `call-ststx-ratio` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz whether the ratio is fetched before or after other state changes in the block across its boundary values through `call-ststx-ratio` in simnet and assert `zip` never returns a value that breaks the invariant.
