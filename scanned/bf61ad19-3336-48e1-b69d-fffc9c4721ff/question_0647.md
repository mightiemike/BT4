# Q0647: Nil or empty recovery fields hash the wrong semantics together via Values Become Identical Only / Different Observers May Supply in Keeper.UpdateUniversalTx

## Question
Can an unprivileged attacker enter through a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id with values that become identical only after trimming, lowercasing, or address canonicalization when different observers may supply formatting variants of the same logical event, and cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so that it abuse a digest rule that treats distinct recovery choices as identical, breaking the invariant that key derivation must preserve every field that affects who can reclaim value, and resulting in Wrong-party refund or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/universal_tx.go::Keeper.UpdateUniversalTx
- Entrypoint: a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id
- Attacker controls: values that become identical only after trimming, lowercasing, or address canonicalization
- Exploit idea: Cause `Keeper.UpdateUniversalTx` to overwrite a different live record than the caller should be able to affect, so it can abuse a digest rule that treats distinct recovery choices as identical.
- Invariant to test: key derivation must preserve every field that affects who can reclaim value
- Expected Immunefi impact: Wrong-party refund or permanent freezing of funds
- Fast validation: write a focused Go test that constructs the two crafted inputs and compare the derived ids plus the downstream replay/finalization behavior
