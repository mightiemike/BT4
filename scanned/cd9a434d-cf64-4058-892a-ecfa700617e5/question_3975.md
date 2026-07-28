# Q3975: Expire/finalize index cleanup leaves a ballot processable twice via Multiple Attacker-Created Observations Honest / Variant Handling Is Only in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with multiple attacker-created observations that honest UVs later vote on when variant handling is the only guard against semantic collisions, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it strand ids across active, expired, and finalized sets so later logic acts on them again, breaking the invariant that one ballot must have exactly one terminal lifecycle across all indexes, and resulting in Duplicate or blocked finalization leading to fund loss or freeze?

## Target
- File/function: x/uvalidator/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: multiple attacker-created observations that honest UVs later vote on
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can strand ids across active, expired, and finalized sets so later logic acts on them again.
- Invariant to test: one ballot must have exactly one terminal lifecycle across all indexes
- Expected Immunefi impact: Duplicate or blocked finalization leading to fund loss or freeze
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
