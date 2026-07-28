# Q2793: Ballot hook updates the wrong pending record via Observation Variants Differ Only / Observation Outcome Changes Value-Moving in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with observation variants that differ only in formatting, canonicalization, or status fields when the observation outcome changes a value-moving or liveness-critical path, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it make terminal hook logic remove or mutate a different inbound/outbound than the ballot just finalized, breaking the invariant that ballot side effects must remain bound to the exact observation that finalized, and resulting in Direct loss or permanent freeze of funds?

## Target
- File/function: x/uvalidator/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: observation variants that differ only in formatting, canonicalization, or status fields
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can make terminal hook logic remove or mutate a different inbound/outbound than the ballot just finalized.
- Invariant to test: ballot side effects must remain bound to the exact observation that finalized
- Expected Immunefi impact: Direct loss or permanent freeze of funds
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
