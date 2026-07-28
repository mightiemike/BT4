# Q1346: Target-id ambiguity applies one vote to the wrong process or migration via Signer Fields, Authz Wrapping, / Attacker Does Not Already in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with signer fields, authz wrapping, or message ids that would matter if authority checks fail when the attacker does not already control a UV, admin, or governance key, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it shape ids so a vote lands on a different record than intended, breaking the invariant that one TSS vote must map to exactly one intended process or migration, and resulting in Wrong TSS state causing direct loss or frozen funds?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: signer fields, authz wrapping, or message ids that would matter if authority checks fail
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can shape ids so a vote lands on a different record than intended.
- Invariant to test: one TSS vote must map to exactly one intended process or migration
- Expected Immunefi impact: Wrong TSS state causing direct loss or frozen funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
