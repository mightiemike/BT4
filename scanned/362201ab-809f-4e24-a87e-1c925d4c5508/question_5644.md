# Q5644: timestamp/median-time rule via `txs_commitment` (header.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the proof pair it induces the node to build, drive `txs_commitment` in `crates/bitcoin-da/src/spec/header.rs` so that the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set, breaking the invariant that header validation is no weaker than Bitcoin's?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `txs_commitment`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: timestamp/median-time rule - reach `txs_commitment` from that entrypoint and force the divergence where the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `prev_hash`, `verify_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header validation is no weaker than Bitcoin's
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed boundary timestamps and compare against bitcoind
