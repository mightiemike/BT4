# Q4062: next-index via redeem: reuse one price and index snapshot across a batch that mut

## Question
Entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) while controlling `amount` of shares burned, can an unprivileged attacker make `next-index` (mainnet/contracts/vault/v0-vault-stx.clar:379) reuse one price and index snapshot across a batch that mutates state between entries? `next-index` returns the stale `index` unchanged when the accrue pause state is set, instead of reverting, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:379` -> `next-index`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `amount` of shares burned
- Exploit idea: `next-index` returns the stale `index` unchanged when the accrue pause state is set, instead of reverting. Reach it through `redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` of shares burned across its boundary values through `redeem` in simnet and assert `next-index` never returns a value that breaks the invariant.
