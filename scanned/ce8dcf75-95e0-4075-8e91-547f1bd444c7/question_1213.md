# Q1213: Ballot hook updates the wrong pending record via Multiple Attacker-Created Observations Honest / Variant Handling Is Only in MsgAddUniversalValidator.GetSigners

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with multiple attacker-created observations that honest UVs later vote on when variant handling is the only guard against semantic collisions, and cause `MsgAddUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so that it make terminal hook logic remove or mutate a different inbound/outbound than the ballot just finalized, breaking the invariant that ballot side effects must remain bound to the exact observation that finalized, and resulting in Direct loss or permanent freeze of funds?

## Target
- File/function: x/uvalidator/types/msg_add_universal_validator.go::MsgAddUniversalValidator.GetSigners
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: multiple attacker-created observations that honest UVs later vote on
- Exploit idea: Cause `MsgAddUniversalValidator.GetSigners` to derive the wrong effective signer or omit the real principal, so it can make terminal hook logic remove or mutate a different inbound/outbound than the ballot just finalized.
- Invariant to test: ballot side effects must remain bound to the exact observation that finalized
- Expected Immunefi impact: Direct loss or permanent freeze of funds
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
