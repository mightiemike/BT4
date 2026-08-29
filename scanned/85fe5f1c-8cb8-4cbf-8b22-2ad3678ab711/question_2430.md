# Q2430: receive-underlying via redeem: leave the accrual clock stale so a later interval double-c

## Question
Entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) while controlling `min-out`, can an unprivileged attacker make `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) leave the accrual clock stale so a later interval double-counts elapsed time? `receive-underlying` pulls the underlying from a named account, so the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `min-out` across its boundary values through `redeem` in simnet and assert `receive-underlying` never returns a value that breaks the invariant.
