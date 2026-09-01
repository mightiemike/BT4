# Q1086: body builder / parser asymmetry via `full_data` (blob.rs)

## Question
Can an unprivileged attacker who inscribes a reveal whose parsed body deserialises differently under two code paths, controlling the wtxid via nonce grinding, drive `full_data` in `crates/bitcoin-da/src/spec/blob.rs` so that the body the builder writes and the body the parser reads back stop being the same object, breaking the invariant that write and read paths are inverses?

## Target
- File/function: `crates/bitcoin-da/src/spec/blob.rs` -> `full_data`
- Entrypoint: unprivileged party inscribes a reveal whose parsed body deserialises differently under two code paths
- Attacker controls: the wtxid via nonce grinding
- Exploit idea: body builder / parser asymmetry - reach `full_data` from that entrypoint and force the divergence where the body the builder writes and the body the parser reads back stop being the same object; the adjacent symbols in the same file that carry the value are `BlobWithSender`, `sender`, `wtxid`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: write and read paths are inverses
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: round-trip every `DataOnDa` variant through builder and parser
