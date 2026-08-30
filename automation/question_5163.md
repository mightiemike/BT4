# Q5163: refresh via borrow: bind a balance before an accrual and assert against it aft

## Question
`refresh` (mainnet/contracts/market/v0-market-vault.clar:171) rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Can an unprivileged caller of `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), by choosing `receiver`, including a contract principal, use that to bind a balance before an accrual and assert against it after, violating the invariant that every second of elapsed time is charged exactly once, to one index, in one direction and producing permanent freezing of a position that can never be closed?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `borrow` and bind a balance before an accrual and assert against it after.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Snapshot every state variable `refresh` touches, run `borrow` with `receiver`, including a contract principal, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
