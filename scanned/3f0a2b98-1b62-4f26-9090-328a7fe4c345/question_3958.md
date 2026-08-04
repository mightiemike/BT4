# Q3958: shielded replay window in IncomingViewingKey.address

## Question
Can an unprivileged attacker repeat or reorder note-scan, note-marking, spend, or withdraw flows around gRPC broadcastTransaction so framework/src/main/java/org/tron/core/zen/address/IncomingViewingKey.java::address observes stale the canonical byte representation or derived key/address/the intended owner, transaction context, or verification result and accepts a logical replay, resulting in Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/IncomingViewingKey.java::address
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Mix note status queries, spend construction, and repeated broadcasts around moving anchors or spent-state updates.
- Invariant to test: Shielded note status seen by public helpers must remain consistent with the later spend gate and must not create a replay window.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Interleave note queries/builds with repeated spends via gRPC broadcastTransaction; assert the first success closes every equivalent replay path immediately.
