# Q0755: Authz wrapping weakens TSS authority checks via Tss Key Ids, Process / Accepted Tss State Would in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with TSS key ids, process ids, or migration ids chosen to collide with existing state when accepted TSS state would affect live outbound signing or migration, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it reach a TSS mutation or vote through a wrapper path that relaxes intended checks, breaking the invariant that wrappers must not make TSS state user-mutable without the intended authority, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: TSS key ids, process ids, or migration ids chosen to collide with existing state
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can reach a TSS mutation or vote through a wrapper path that relaxes intended checks.
- Invariant to test: wrappers must not make TSS state user-mutable without the intended authority
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
