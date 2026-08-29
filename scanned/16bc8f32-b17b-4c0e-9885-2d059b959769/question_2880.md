# Q2880: receive-tokens via transfer: reuse one price and index snapshot across a batch that mut

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `receive-tokens` (mainnet/contracts/market/v0-market-vault.clar:256) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it pulls an asset from a named account, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:256` -> `receive-tokens`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `receive-tokens` pulls an asset from a named account. Reach it through `transfer` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `transfer` in simnet and assert `receive-tokens` never returns a value that breaks the invariant.
