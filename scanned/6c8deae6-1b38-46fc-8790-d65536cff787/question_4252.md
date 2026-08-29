# Q4252: resolve-dia via liquidate-multi: pass a health check and then change, in the same transacti

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `resolve-dia` (mainnet/contracts/market/v0-4-market.clar:326) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it derives a (string-ascii 32) key from a (buff 32) ident, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:326` -> `resolve-dia`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `resolve-dia` derives a (string-ascii 32) key from a (buff 32) ident. Reach it through `liquidate-multi` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `liquidate-multi` with the trait principals supplied per entry, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
