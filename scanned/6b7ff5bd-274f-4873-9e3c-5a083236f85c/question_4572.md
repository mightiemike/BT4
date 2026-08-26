# Q4572: vote_state_handler::set_commission - commission changed beyond bounds or without authority

## Question
Can an unprivileged attacker who submits vote instructions that drive vote-state version conversion and authority updates, storing a vote account in an older version format and converting it in the same block, drive `vote_state_handler::set_commission` to set a commission or basis-points value outside the valid range or without the withdrawer's signature, so that the invariant that commission is bounded and only the authorized withdrawer may change it is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `set_commission`
- Entrypoint: submits vote instructions that drive vote-state version conversion and authority updates, storing a vote account in an older version format and converting it in the same block
- Attacker controls: the stored vote state version and bytes, the proposed authorities, commission values and credit history
- Exploit idea: Set a commission or basis-points value outside the valid range or without the withdrawer's signature.
- Invariant to test: Commission is bounded and only the authorized withdrawer may change it.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the handler conversion and setter with the crafted state and assert fields are preserved and bounded
