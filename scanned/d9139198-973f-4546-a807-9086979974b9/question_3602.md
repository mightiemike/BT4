# Q3602: Formatting-only variants bypass replay protection or split history via Cross-Chain Identifiers Are Semantically / One Collision Split Would in Keeper.UpdateUniversalTx

## Question
Can an unprivileged attacker enter through a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id with cross-chain identifiers that are semantically different but hash to the same derived record if canonicalization is too lenient when one collision or split would affect a value-moving lifecycle, and cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so that it represent the same logical event two ways so one path sees a replay and another sees a fresh action, breaking the invariant that derived-key logic must give one stable identity to one logical event across all callers, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/universal_tx.go::Keeper.UpdateUniversalTx
- Entrypoint: a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id
- Attacker controls: cross-chain identifiers that are semantically different but hash to the same derived record if canonicalization is too lenient
- Exploit idea: Cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so it can represent the same logical event two ways so one path sees a replay and another sees a fresh action.
- Invariant to test: derived-key logic must give one stable identity to one logical event across all callers
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a focused Go test that constructs the two crafted inputs and compare the derived ids plus the downstream replay/finalization behavior
