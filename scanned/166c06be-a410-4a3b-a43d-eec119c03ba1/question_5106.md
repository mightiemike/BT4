# Q5106: double-counted reveal in one block via `calculate_new_difficulty` (verifier.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the block's transaction set and coinbase, drive `calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` so that the number of times a reveal is processed and the number of times it appears in the block stop being equal, breaking the invariant that each reveal is processed exactly once per block?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `calculate_new_difficulty`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: double-counted reveal in one block - reach `calculate_new_difficulty` from that entrypoint and force the divergence where the number of times a reveal is processed and the number of times it appears in the block stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each reveal is processed exactly once per block
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: duplicate a reveal shape and assert single processing
