# Q1931: journal field packing via `main` (batch_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling the L1 payload the prover must ingest, drive `main` in `guests/risc0/batch-proof/bitcoin/src/bin/batch_proof_bitcoin.rs` so that the journal fields the guest commits and the fields the verifier decodes stop being the same layout, breaking the invariant that journal encoding is canonical?

## Target
- File/function: `guests/risc0/batch-proof/bitcoin/src/bin/batch_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: the L1 payload the prover must ingest
- Exploit idea: journal field packing - reach `main` from that entrypoint and force the divergence where the journal fields the guest commits and the fields the verifier decodes stop being the same layout; the adjacent symbols in the same file that carry the value are `get_forks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: journal encoding is canonical
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: round-trip journals across versions
