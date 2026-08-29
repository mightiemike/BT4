# Q0696: resolve-or-create via borrow: bind a balance before an accrual and assert against it aft

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the order of accrual versus price resolution inside the let reach `resolve-or-create` (mainnet/contracts/market/v0-market-vault.clar:143) in a state where it bind a balance before an accrual and assert against it after? Given that it allocates a user id through `increment` for whatever principal the market names, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:143` -> `resolve-or-create`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the order of accrual versus price resolution inside the let
- Exploit idea: `resolve-or-create` allocates a user id through `increment` for whatever principal the market names. Reach it through `borrow` and bind a balance before an accrual and assert against it after.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the order of accrual versus price resolution inside the let across its boundary values through `borrow` in simnet and assert `resolve-or-create` never returns a value that breaks the invariant.
