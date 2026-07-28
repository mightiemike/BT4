# Q0427: Quorum recompute revives or flips a terminal ballot incorrectly via Sequence Of Deposits Outbounds / Variant Handling Is Only in MsgRemoveUniversalValidator.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with a sequence of deposits or outbounds meant to keep ballots pending, expired, or recomputed when variant handling is the only guard against semantic collisions, and cause `MsgRemoveUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so that it use validator-set changes and attacker-created observations to move a terminal ballot to a new result, breaking the invariant that terminal ballot results must remain stable or recompute only under strictly safe rules, and resulting in Wrong finalization causing direct loss or permanent freezing?

## Target
- File/function: x/uvalidator/types/msg_remove_universal_validator.go::MsgRemoveUniversalValidator.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: a sequence of deposits or outbounds meant to keep ballots pending, expired, or recomputed
- Exploit idea: Cause `MsgRemoveUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so it can use validator-set changes and attacker-created observations to move a terminal ballot to a new result.
- Invariant to test: terminal ballot results must remain stable or recompute only under strictly safe rules
- Expected Immunefi impact: Wrong finalization causing direct loss or permanent freezing
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
