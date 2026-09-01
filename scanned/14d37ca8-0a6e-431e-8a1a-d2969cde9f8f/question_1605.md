# Q1605: skip-path determinism via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path, controlling how many blobs land in one block, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the journal produced when a blob is skipped by `continue` and the journal another prover produces stop being the same, breaking the invariant that every skip decision is a pure function of the blob and prior state?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path
- Attacker controls: how many blobs land in one block
- Exploit idea: skip-path determinism - reach `main` from that entrypoint and force the divergence where the journal produced when a blob is skipped by `continue` and the journal another prover produces stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every skip decision is a pure function of the blob and prior state
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: run two provers with different ingestion orders and diff outputs
