# Q3977: Expire/finalize index cleanup leaves a ballot processable twice via Sequence Of Deposits Outbounds / Variant Handling Is Only in MsgUpdateUniversalValidatorStatus.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with a sequence of deposits or outbounds meant to keep ballots pending, expired, or recomputed when variant handling is the only guard against semantic collisions, and cause `MsgUpdateUniversalValidatorStatus.GetSigners` to derive the wrong effective signer or omit the real principal, so that it strand ids across active, expired, and finalized sets so later logic acts on them again, breaking the invariant that one ballot must have exactly one terminal lifecycle across all indexes, and resulting in Duplicate or blocked finalization leading to fund loss or freeze?

## Target
- File/function: x/uvalidator/types/msg_update_universal_validator_status.go::MsgUpdateUniversalValidatorStatus.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: a sequence of deposits or outbounds meant to keep ballots pending, expired, or recomputed
- Exploit idea: Cause `MsgUpdateUniversalValidatorStatus.GetSigners` to derive the wrong effective signer or omit the real principal, so it can strand ids across active, expired, and finalized sets so later logic acts on them again.
- Invariant to test: one ballot must have exactly one terminal lifecycle across all indexes
- Expected Immunefi impact: Duplicate or blocked finalization leading to fund loss or freeze
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
