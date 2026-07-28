# Q0006: Ballot identity collision merges distinct observations via Observation Variants Differ Only / Variant Handling Is Only in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with observation variants that differ only in formatting, canonicalization, or status fields when variant handling is the only guard against semantic collisions, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make two semantically different observations land on one ballot id, breaking the invariant that one ballot id must correspond to exactly one security-relevant observation meaning, and resulting in Wrong finalization leading to direct loss or permanent freezing of funds?

## Target
- File/function: x/uvalidator/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: observation variants that differ only in formatting, canonicalization, or status fields
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make two semantically different observations land on one ballot id.
- Invariant to test: one ballot id must correspond to exactly one security-relevant observation meaning
- Expected Immunefi impact: Wrong finalization leading to direct loss or permanent freezing of funds
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
