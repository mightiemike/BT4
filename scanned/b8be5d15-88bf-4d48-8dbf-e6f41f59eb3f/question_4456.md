# Q4456: vote_processor::is_vote_authorize_with_bls_enabled - BLS authorization accepted without the corresponding key (submitting in the slot where the)

## Question
Can an unprivileged attacker who creates its own vote account and submits vote-program instructions to it, submitting in the slot where the governing vote feature gate activates, drive `vote_processor::is_vote_authorize_with_bls_enabled` to authorize a BLS voter identity the attacker does not hold the key for, so that the invariant that BLS voter authorization requires proof of the corresponding key is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/vote/src/vote_processor.rs` -> `is_vote_authorize_with_bls_enabled`
- Entrypoint: creates its own vote account and submits vote-program instructions to it, submitting in the slot where the governing vote feature gate activates
- Attacker controls: the vote account contents, authorized voter and withdrawer keys, seeds, and instruction data
- Exploit idea: Authorize a BLS voter identity the attacker does not hold the key for.
- Invariant to test: BLS voter authorization requires proof of the corresponding key.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the vote instruction against the crafted account and assert the authority check rejects it
