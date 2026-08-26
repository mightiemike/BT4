# Q4484: vote_state::check_and_filter_proposed_vote_state - tower depth exceeded so history is corrupted

## Question
Can an unprivileged attacker who submits vote-state updates to a vote account it created and funded, submitting a tower whose slots are all older than the stored root, drive `vote_state::check_and_filter_proposed_vote_state` to submit a tower longer than the maximum lockout history so entries are dropped or overwritten, so that the invariant that committed towers never exceed the maximum lockout history is broken and the outcome is Consensus/Safety Violation (invalid optimistic confirmation or rooted slot)?

## Target
- File/function: `programs/vote/src/vote_state/mod.rs` -> `check_and_filter_proposed_vote_state`
- Entrypoint: submits vote-state updates to a vote account it created and funded, submitting a tower whose slots are all older than the stored root
- Attacker controls: the proposed lockout tower, slot list, root slot, timestamps and credits in the vote instruction
- Exploit idea: Submit a tower longer than the maximum lockout history so entries are dropped or overwritten.
- Invariant to test: Committed towers never exceed the maximum lockout history.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (invalid optimistic confirmation or rooted slot)
- Fast validation: unit-test process_new_vote_state with the crafted tower and assert the lockout and ordering invariants are enforced
