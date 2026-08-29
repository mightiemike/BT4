# Q0924: total-debt via redeem: strand value on the market contract when a later step of a

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls the vault's available liquidity relative to the redemption reach `total-debt` (mainnet/contracts/vault/v0-vault-stx.clar:328) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it computes cumulative debt from `principal-scaled` and `index`, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:328` -> `total-debt`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: the vault's available liquidity relative to the redemption
- Exploit idea: `total-debt` computes cumulative debt from `principal-scaled` and `index`. Reach it through `redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the vault's available liquidity relative to the redemption across its boundary values through `redeem` in simnet and assert `total-debt` never returns a value that breaks the invariant.
