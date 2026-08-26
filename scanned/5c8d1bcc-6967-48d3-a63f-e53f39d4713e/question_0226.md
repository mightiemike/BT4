# Q226: sigverify::ed25519_verify_serial - offset arithmetic overflow on 64-bit boundaries (marking the packet with the flags)

## Question
Can an unprivileged attacker who sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path, drive `sigverify::ed25519_verify_serial` to supply signature/pubkey/message offsets whose sums wrap so verification reads a chosen in-buffer region, so that the invariant that every offset plus length is checked against the packet length without wrapping is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `perf/src/sigverify.rs` -> `ed25519_verify_serial`
- Entrypoint: sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path
- Attacker controls: the complete packet bytes, packet batch sizes and the declared signature/pubkey offsets
- Exploit idea: Supply signature/pubkey/message offsets whose sums wrap so verification reads a chosen in-buffer region.
- Invariant to test: Every offset plus length is checked against the packet length without wrapping.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_packet on the crafted packet and assert it is marked discard and never reaches the bank
