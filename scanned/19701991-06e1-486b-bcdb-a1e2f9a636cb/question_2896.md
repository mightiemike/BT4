# Q2896: identifier-reuse replay in ECKeyFactory.getInstance

## Question
Can an unprivileged attacker reach /wallet/broadcasttransaction so crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance accepts the same digest, signature-derived id, or replay-protection identifier more than once, breaking one-time semantics and causing Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for alternate encodings, restart windows, or map-key normalization that let one logical identifier be reused.
- Invariant to test: Replay-protection identifiers and security-critical digests must map one-to-one to one completed action.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Replay the same logical identifier through every supported encoding and public surface around /wallet/broadcasttransaction; assert the first accepted use closes all equivalent forms.
