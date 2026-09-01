# Q0956: chunk reassembly identity via `decompress_chunks` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a maximally compressible body to probe the decompression bound, controlling compression ratio and body length, drive `decompress_chunks` in `crates/bitcoin-da/src/spec/mod.rs` so that the concatenation of chunks and the original complete body stop being the same bytes, breaking the invariant that reassembly is exact and order-bound?

## Target
- File/function: `crates/bitcoin-da/src/spec/mod.rs` -> `decompress_chunks`
- Entrypoint: unprivileged party inscribes a maximally compressible body to probe the decompression bound
- Attacker controls: compression ratio and body length
- Exploit idea: chunk reassembly identity - reach `decompress_chunks` from that entrypoint and force the divergence where the concatenation of chunks and the original complete body stop being the same bytes; the adjacent symbols in the same file that carry the value are `BitcoinSpec`, `RollupParams`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reassembly is exact and order-bound
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: reorder or substitute chunks and assert rejection
