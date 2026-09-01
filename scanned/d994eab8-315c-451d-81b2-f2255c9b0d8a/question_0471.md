# Q0471: proof session reuse via `get_light_client_elfs` (bitcoin.rs)

## Question
Can an unprivileged attacker who makes the proved range span a fork or method-id activation boundary, controlling the overlap between requested ranges, drive `get_light_client_elfs` in `bin/citrea/src/rollup/bitcoin.rs` so that the input a proving session was started for and the input its output is attributed to stop being the same, breaking the invariant that each proof output is bound to its input?

## Target
- File/function: `bin/citrea/src/rollup/bitcoin.rs` -> `get_light_client_elfs`
- Entrypoint: unprivileged party makes the proved range span a fork or method-id activation boundary
- Attacker controls: the overlap between requested ranges
- Exploit idea: proof session reuse - reach `get_light_client_elfs` from that entrypoint and force the divergence where the input a proving session was started for and the input its output is attributed to stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinRollup`, `create_rpc_methods`, `create_storage_manager`, `create_da_service`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof output is bound to its input
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: interleave sessions and assert attribution
