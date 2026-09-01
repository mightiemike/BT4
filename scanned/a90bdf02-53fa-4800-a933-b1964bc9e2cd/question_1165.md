# Q1165: sender check applied to the wrong field via `run` (da_block_handler.rs)

## Question
Can an unprivileged attacker who inscribes near-miss encodings that sit on the parse/skip boundary, controlling the exact byte encoding on the parse boundary, drive `run` in `crates/light-client-prover/src/da_block_handler.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `crates/light-client-prover/src/da_block_handler.rs` -> `run`
- Entrypoint: unprivileged party inscribes near-miss encodings that sit on the parse/skip boundary
- Attacker controls: the exact byte encoding on the parse boundary
- Exploit idea: sender check applied to the wrong field - reach `run` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are `L1BlockHandler`, `process_queued_l1_blocks`, `process_l1_block`, `prove`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
