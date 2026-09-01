# Q4446: timestamp/median-time rule via `verify_header_chain_mainnet` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling header fields at the boundary, drive `verify_header_chain_mainnet` in `crates/bitcoin-da/src/verifier.rs` so that the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set, breaking the invariant that header validation is no weaker than Bitcoin's?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_header_chain_mainnet`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: header fields at the boundary
- Exploit idea: timestamp/median-time rule - reach `verify_header_chain_mainnet` from that entrypoint and force the divergence where the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header validation is no weaker than Bitcoin's
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed boundary timestamps and compare against bitcoind
