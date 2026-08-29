# Q1452: accrue-debt-asset via redeem: strand value on the market contract when a later step of a

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `recipient` reach `accrue-debt-asset` (mainnet/contracts/market/v0-4-market.clar:262) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it calls `accrue-and-cache` with `unwrap-panic` inside a fold whose accumulator ignores the result, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:262` -> `accrue-debt-asset`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `accrue-debt-asset` calls `accrue-and-cache` with `unwrap-panic` inside a fold whose accumulator ignores the result. Reach it through `redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `recipient` across its boundary values through `redeem` in simnet and assert `accrue-debt-asset` never returns a value that breaks the invariant.
