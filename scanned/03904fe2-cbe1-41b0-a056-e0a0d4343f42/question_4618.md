# Q4618: vote_state_handler::commission - commission changed beyond bounds or without authority (sizing the vote account exactly at)

## Question
Can an unprivileged attacker who submits vote instructions that drive vote-state version conversion and authority updates, sizing the vote account exactly at the maximum serialized state length, drive `vote_state_handler::commission` to set a commission or basis-points value outside the valid range or without the withdrawer's signature, so that the invariant that commission is bounded and only the authorized withdrawer may change it is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `commission`
- Entrypoint: submits vote instructions that drive vote-state version conversion and authority updates, sizing the vote account exactly at the maximum serialized state length
- Attacker controls: the stored vote state version and bytes, the proposed authorities, commission values and credit history
- Exploit idea: Set a commission or basis-points value outside the valid range or without the withdrawer's signature.
- Invariant to test: Commission is bounded and only the authorized withdrawer may change it.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the handler conversion and setter with the crafted state and assert fields are preserved and bounded
