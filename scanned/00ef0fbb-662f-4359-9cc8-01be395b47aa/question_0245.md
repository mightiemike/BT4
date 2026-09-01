# Q0245: l2 genesis root assumption via `citrea_network_to_chain_id` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the initial and final roots the offered data claims, drive `citrea_network_to_chain_id` in `crates/light-client-prover/src/circuit/mod.rs` so that the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal, breaking the invariant that the bootstrap root is pinned?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `citrea_network_to_chain_id`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: l2 genesis root assumption - reach `citrea_network_to_chain_id` from that entrypoint and force the divergence where the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the bootstrap root is pinned
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: start with no previous output and assert the pinned root
