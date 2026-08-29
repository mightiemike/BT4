# Q5444: iter-lookup-debt via collateral-add: reuse one price and index snapshot across a batch that mut

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the `ft` trait principal reach `iter-lookup-debt` (mainnet/contracts/market/v0-market-vault.clar:218) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:218` -> `iter-lookup-debt`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `iter-lookup-debt` skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position. Reach it through `collateral-add` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the `ft` trait principal varied, and assert that the value `iter-lookup-debt` returns is identical in both runs; a divergence confirms the finding.
