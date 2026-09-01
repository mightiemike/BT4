# Q0239: transaction authentication in the runtime via `deserialize_hex_vec` (genesis.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `deserialize_hex_vec` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/genesis.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/genesis.rs` -> `deserialize_hex_vec`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: transaction authentication in the runtime - reach `deserialize_hex_vec` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `AccountConfig`, `init_module`, `create_default_account`, `exit_if_address_exists`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
