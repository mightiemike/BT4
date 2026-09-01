# Q0946: compression bomb versus size bound via `decompress_chunks` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a maximally compressible body to probe the decompression bound, controlling compression ratio and body length, drive `decompress_chunks` in `crates/bitcoin-da/src/spec/mod.rs` so that the decompressed body size and the size the protocol bounds stop being consistent, breaking the invariant that decompression output is bounded before use?

## Target
- File/function: `crates/bitcoin-da/src/spec/mod.rs` -> `decompress_chunks`
- Entrypoint: unprivileged party inscribes a maximally compressible body to probe the decompression bound
- Attacker controls: compression ratio and body length
- Exploit idea: compression bomb versus size bound - reach `decompress_chunks` from that entrypoint and force the divergence where the decompressed body size and the size the protocol bounds stop being consistent; the adjacent symbols in the same file that carry the value are `BitcoinSpec`, `RollupParams`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: decompression output is bounded before use
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: inscribe a maximal-ratio body and assert bounded handling
