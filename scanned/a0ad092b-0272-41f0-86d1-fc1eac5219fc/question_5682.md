# Q5682: send-tokens via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling which collateral and debt asset pair is targeted, can an unprivileged attacker make `send-tokens` (mainnet/contracts/market/v0-market-vault.clar:259) reuse one price and index snapshot across a batch that mutates state between entries? `send-tokens` pushes an asset to a caller-chosen recipient principal, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:259` -> `send-tokens`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `send-tokens` pushes an asset to a caller-chosen recipient principal. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz which collateral and debt asset pair is targeted across its boundary values through `liquidate` in simnet and assert `send-tokens` never returns a value that breaks the invariant.
