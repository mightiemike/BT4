# Q0559: transaction authentication in the runtime via `test_config_serialization` (genesis.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `test_config_serialization` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/genesis.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/genesis.rs` -> `test_config_serialization`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: transaction authentication in the runtime - reach `test_config_serialization` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `AccountConfig`, `deserialize_hex_vec`, `init_module`, `create_default_account`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
