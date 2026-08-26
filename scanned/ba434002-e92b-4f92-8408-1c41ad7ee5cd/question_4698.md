# Q4698: vote_state_handler::set_new_authorized_voter - authorized voter set outside its epoch lock (setting commission in the slot where)

## Question
Can an unprivileged attacker who submits vote instructions that drive vote-state version conversion and authority updates, setting commission in the slot where the commission feature gate activates, drive `vote_state_handler::set_new_authorized_voter` to set a new authorized voter for an epoch the lock should protect, so that the invariant that an authorized voter change takes effect only at the permitted epoch boundary is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `set_new_authorized_voter`
- Entrypoint: submits vote instructions that drive vote-state version conversion and authority updates, setting commission in the slot where the commission feature gate activates
- Attacker controls: the stored vote state version and bytes, the proposed authorities, commission values and credit history
- Exploit idea: Set a new authorized voter for an epoch the lock should protect.
- Invariant to test: An authorized voter change takes effect only at the permitted epoch boundary.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the handler conversion and setter with the crafted state and assert fields are preserved and bounded
