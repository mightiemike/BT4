# Q5948: convert-to-assets-preview via liquidate-redeem: consume a cache entry after the vault it describes has alr

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `convert-to-assets-preview` (mainnet/contracts/vault/v0-vault-stx.clar:317) in a state where it consume a cache entry after the vault it describes has already moved? Given that it prices a redemption against `total-assets-preview` and `total-supply-preview`, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:317` -> `convert-to-assets-preview`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `convert-to-assets-preview` prices a redemption against `total-assets-preview` and `total-supply-preview`. Reach it through `liquidate-redeem` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the redemption receiver varied, and assert that the value `convert-to-assets-preview` returns is identical in both runs; a divergence confirms the finding.
