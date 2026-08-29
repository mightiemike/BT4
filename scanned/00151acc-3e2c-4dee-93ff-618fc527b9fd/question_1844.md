# Q1844: get-cached-indexes via accrue: strand value on the market contract when a later step of a

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls whether an earlier call in the same block already advanced last-update reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: whether an earlier call in the same block already advanced last-update
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `accrue` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `accrue` twice with whether an earlier call in the same block already advanced last-update varied, and assert that the value `get-cached-indexes` returns is identical in both runs; a divergence confirms the finding.
