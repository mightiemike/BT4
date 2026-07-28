# Q2814: Push-origin and external-origin UTX keys diverge for one lifecycle via Values Become Identical Only / Attacker Can Create Multiple in Keeper.UpdateUniversalTx

## Question
Can an unprivileged attacker enter through a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id with values that become identical only after trimming, lowercasing, or address canonicalization when the attacker can create multiple candidate events or observations, and cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so that it make one logical lifecycle obtain more than one UTX key or one key cover more than one lifecycle, breaking the invariant that UTX-key derivation must remain one-to-one across origin types, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/universal_tx.go::Keeper.UpdateUniversalTx
- Entrypoint: a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id
- Attacker controls: values that become identical only after trimming, lowercasing, or address canonicalization
- Exploit idea: Cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so it can make one logical lifecycle obtain more than one UTX key or one key cover more than one lifecycle.
- Invariant to test: UTX-key derivation must remain one-to-one across origin types
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a focused Go test that constructs the two crafted inputs and compare the derived ids plus the downstream replay/finalization behavior
