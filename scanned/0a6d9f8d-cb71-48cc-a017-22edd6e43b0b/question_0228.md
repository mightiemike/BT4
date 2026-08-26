# Q228: sigverify::ed25519_verify - malleable signature encodings accepted (marking the packet with the flags)

## Question
Can an unprivileged attacker who sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path, drive `sigverify::ed25519_verify` to submit two distinct signature encodings for the same message so the transaction can be admitted twice under different signatures, so that the invariant that each accepted transaction has exactly one verifying signature encoding is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `perf/src/sigverify.rs` -> `ed25519_verify`
- Entrypoint: sends raw transaction packets to a validator's TPU/QUIC ingress, marking the packet with the flags that route it through the simple-vote fast path
- Attacker controls: the complete packet bytes, packet batch sizes and the declared signature/pubkey offsets
- Exploit idea: Submit two distinct signature encodings for the same message so the transaction can be admitted twice under different signatures.
- Invariant to test: Each accepted transaction has exactly one verifying signature encoding.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test verify_packet on the crafted packet and assert it is marked discard and never reaches the bank
