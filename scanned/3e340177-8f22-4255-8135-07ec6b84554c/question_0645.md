# Q0645: Nil or empty recovery fields hash the wrong semantics together via Fields Feed Directly Into / Different Observers May Supply in Keeper.BuildPcUniversalTxKey

## Question
Can an unprivileged attacker enter through a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id with fields that feed directly into UTX ids, ballot ids, revert ids, rescue ids, or reverse-lookup keys when different observers may supply formatting variants of the same logical event, and cause `Keeper.BuildPcUniversalTxKey` to bind a new record or derived action to the wrong live context, so that it abuse a digest rule that treats distinct recovery choices as identical, breaking the invariant that key derivation must preserve every field that affects who can reclaim value, and resulting in Wrong-party refund or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/universal_tx.go::Keeper.BuildPcUniversalTxKey
- Entrypoint: a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id
- Attacker controls: fields that feed directly into UTX ids, ballot ids, revert ids, rescue ids, or reverse-lookup keys
- Exploit idea: Cause `Keeper.BuildPcUniversalTxKey` to bind a new record or derived action to the wrong live context, so it can abuse a digest rule that treats distinct recovery choices as identical.
- Invariant to test: key derivation must preserve every field that affects who can reclaim value
- Expected Immunefi impact: Wrong-party refund or permanent freezing of funds
- Fast validation: write a focused Go test that constructs the two crafted inputs and compare the derived ids plus the downstream replay/finalization behavior
