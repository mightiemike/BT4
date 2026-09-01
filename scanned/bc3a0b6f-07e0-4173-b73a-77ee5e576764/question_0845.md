# Q0845: sender check applied to the wrong field via `process_l1_block` (da_block_handler.rs)

## Question
Can an unprivileged attacker who times inscriptions so two honest provers observe different blob ordering, controlling blob ordering inside the L1 block, drive `process_l1_block` in `crates/light-client-prover/src/da_block_handler.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `crates/light-client-prover/src/da_block_handler.rs` -> `process_l1_block`
- Entrypoint: unprivileged party times inscriptions so two honest provers observe different blob ordering
- Attacker controls: blob ordering inside the L1 block
- Exploit idea: sender check applied to the wrong field - reach `process_l1_block` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are `L1BlockHandler`, `run`, `process_queued_l1_blocks`, `prove`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
