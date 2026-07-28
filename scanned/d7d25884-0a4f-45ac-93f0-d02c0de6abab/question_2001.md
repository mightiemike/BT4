# Q2001: Quorum recompute revives or flips a terminal ballot incorrectly via Observation Variants Differ Only / Observation Outcome Changes Value-Moving in MsgAddUniversalValidator.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with observation variants that differ only in formatting, canonicalization, or status fields when the observation outcome changes a value-moving or liveness-critical path, and cause `MsgAddUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so that it use validator-set changes and attacker-created observations to move a terminal ballot to a new result, breaking the invariant that terminal ballot results must remain stable or recompute only under strictly safe rules, and resulting in Wrong finalization causing direct loss or permanent freezing?

## Target
- File/function: x/uvalidator/types/msg_add_universal_validator.go::MsgAddUniversalValidator.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: observation variants that differ only in formatting, canonicalization, or status fields
- Exploit idea: Cause `MsgAddUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so it can use validator-set changes and attacker-created observations to move a terminal ballot to a new result.
- Invariant to test: terminal ballot results must remain stable or recompute only under strictly safe rules
- Expected Immunefi impact: Wrong finalization causing direct loss or permanent freezing
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
