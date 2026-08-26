# Q217: sigverify::ed25519_verify - batch vs serial verification disagree (marking the packet with the flags)

## Question
Can an unprivileged attacker who sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path, drive `sigverify::ed25519_verify` to craft a packet that the batched path accepts and the serial path rejects (or the reverse), so that the invariant that batched and serial verification produce identical accept/reject decisions for every packet is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `perf/src/sigverify.rs` -> `ed25519_verify`
- Entrypoint: sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path
- Attacker controls: the complete packet bytes, packet batch sizes and the declared signature/pubkey offsets
- Exploit idea: Craft a packet that the batched path accepts and the serial path rejects (or the reverse).
- Invariant to test: Batched and serial verification produce identical accept/reject decisions for every packet.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test verify_packet on the crafted packet and assert it is marked discard and never reaches the bank
