# Q1615: blob ordering dependence via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes near-miss encodings that sit on the parse/skip boundary, controlling blob ordering inside the L1 block, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the output for a block processed in one blob order and the output for the same block in another order stop being equal, breaking the invariant that the journal is order-independent or the order is canonical?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes near-miss encodings that sit on the parse/skip boundary
- Attacker controls: blob ordering inside the L1 block
- Exploit idea: blob ordering dependence - reach `main` from that entrypoint and force the divergence where the output for a block processed in one blob order and the output for the same block in another order stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the journal is order-independent or the order is canonical
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: permute blobs within a block and diff journals
