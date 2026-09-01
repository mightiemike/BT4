# Q4853: signature malleability in sov-keys via `genesis_config` (runtime.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `genesis_config` in `crates/citrea-stf/src/runtime.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/citrea-stf/src/runtime.rs` -> `genesis_config`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: signature malleability in sov-keys - reach `genesis_config` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `CitreaRuntime`, `rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
