# Q4412: make_next_pox_id: block accepted with signer weight below threshold

## Question
Can an unprivileged attacker reach `make_next_pox_id` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `verify_signer_signatures`/`get_signers_weights` counts a duplicate or wrong-set signature, breaking the invariant that summed distinct valid signer weight >= threshold from that cycle's set — leading to finalising an unsigned block?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `make_next_pox_id`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `verify_signer_signatures`/`get_signers_weights` counts a duplicate or wrong-set signature
- Invariant to test: summed distinct valid signer weight >= threshold from that cycle's set
- Expected Immunefi impact: Critical - finalising an unsigned block
- Fast validation: test an under-weight block
