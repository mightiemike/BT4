# Q0992: Canonicalization collapses safe and unsafe variants into one tally via Vote-Bearing Messages If Signer / Observation Outcome Changes Value-Moving in msgServer.UpdateUniversalValidator

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with vote-bearing messages if signer restrictions can be bypassed by an unprivileged account when the observation outcome changes a value-moving or liveness-critical path, and cause `msgServer.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so that it change formatting-sensitive fields until honest voters appear to agree on different semantics, breaking the invariant that variant handling must preserve every field that changes execution outcome, and resulting in Wrong finalization with direct loss or permanent freezing?

## Target
- File/function: x/uvalidator/keeper/msg_server.go::msgServer.UpdateUniversalValidator
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: vote-bearing messages if signer restrictions can be bypassed by an unprivileged account
- Exploit idea: Cause `msgServer.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so it can change formatting-sensitive fields until honest voters appear to agree on different semantics.
- Invariant to test: variant handling must preserve every field that changes execution outcome
- Expected Immunefi impact: Wrong finalization with direct loss or permanent freezing
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
