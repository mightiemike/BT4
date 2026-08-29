# Q5353: socialize-debt via repay: reuse one price and index snapshot across a batch that mut

## Question
Can an unprivileged attacker entering through `repay` (mainnet/contracts/market/v0-4-market.clar:1316), controlling `on-behalf-of`, naming any third-party principal, drive `socialize-debt` (mainnet/contracts/vault/v0-vault-stx.clar:944) — which writes down `lindex` by one ratio while reducing `assets` by a completely different `principal-reduction` — to reuse one price and index snapshot across a batch that mutates state between entries, breaking the invariant that every second of elapsed time is charged exactly once, to one index, in one direction, and cause direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:944` -> `socialize-debt`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `on-behalf-of`, naming any third-party principal
- Exploit idea: `socialize-debt` writes down `lindex` by one ratio while reducing `assets` by a completely different `principal-reduction`. Reach it through `repay` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `repay` with `on-behalf-of`, naming any third-party principal, then read `socialize-debt` state before and after in the same block and assert the two sides of the invariant are equal.
