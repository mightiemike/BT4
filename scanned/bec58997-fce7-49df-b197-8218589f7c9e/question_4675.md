# Q4675: vote_state_handler::nth_recent_lockout - lockout expiry mishandled so votes never expire (changing the authorized voter twice within)

## Question
Can an unprivileged attacker who submits vote instructions that drive vote-state version conversion and authority updates, changing the authorized voter twice within one epoch, drive `vote_state_handler::nth_recent_lockout` to make pop_expired_votes or double_lockouts leave stale lockouts in place, so that the invariant that expired lockouts are removed and doubling follows the protocol rule is broken and the outcome is Consensus/Safety Violation (invalid optimistic confirmation or rooted slot)?

## Target
- File/function: `programs/vote/src/vote_state/handler.rs` -> `nth_recent_lockout`
- Entrypoint: submits vote instructions that drive vote-state version conversion and authority updates, changing the authorized voter twice within one epoch
- Attacker controls: the stored vote state version and bytes, the proposed authorities, commission values and credit history
- Exploit idea: Make pop_expired_votes or double_lockouts leave stale lockouts in place.
- Invariant to test: Expired lockouts are removed and doubling follows the protocol rule.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (invalid optimistic confirmation or rooted slot)
- Fast validation: unit-test the handler conversion and setter with the crafted state and assert fields are preserved and bounded
