# Q4707: vote_state_handler::set_authorized_withdrawer - rewards collector redirected (setting commission in the slot where)

## Question
Can an unprivileged attacker who submits vote instructions that drive vote-state version conversion and authority updates, setting commission in the slot where the commission feature gate activates, drive `vote_state_handler::set_authorized_withdrawer` to set the inflation or block revenue collector to an address the withdrawer never authorized, so that the invariant that reward collectors are set only by the authorized withdrawer is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `set_authorized_withdrawer`
- Entrypoint: submits vote instructions that drive vote-state version conversion and authority updates, setting commission in the slot where the commission feature gate activates
- Attacker controls: the stored vote state version and bytes, the proposed authorities, commission values and credit history
- Exploit idea: Set the inflation or block revenue collector to an address the withdrawer never authorized.
- Invariant to test: Reward collectors are set only by the authorized withdrawer.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the handler conversion and setter with the crafted state and assert fields are preserved and bounded
