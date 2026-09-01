# Q5756: tapscript push-boundary body split via `sender` (blob.rs)

## Question
Can an unprivileged attacker who inscribes a reveal whose parsed body deserialises differently under two code paths, controlling the envelope/tapscript layout, drive `sender` in `crates/bitcoin-da/src/spec/blob.rs` so that the body reassembled from script pushes and the body originally serialised stop being identical, breaking the invariant that push segmentation does not change contents?

## Target
- File/function: `crates/bitcoin-da/src/spec/blob.rs` -> `sender`
- Entrypoint: unprivileged party inscribes a reveal whose parsed body deserialises differently under two code paths
- Attacker controls: the envelope/tapscript layout
- Exploit idea: tapscript push-boundary body split - reach `sender` from that entrypoint and force the divergence where the body reassembled from script pushes and the body originally serialised stop being identical; the adjacent symbols in the same file that carry the value are `BlobWithSender`, `wtxid`, `full_data`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: push segmentation does not change contents
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: split a body across push boundaries adversarially and round-trip
