# Q2608: identifier-reuse replay in ByteArrayMap.hashCode

## Question
Can an unprivileged attacker reach /wallet/* public HTTP APIs so common/src/main/java/org/tron/common/utils/ByteArrayMap.java::hashCode accepts the same digest, signature-derived id, or replay-protection identifier more than once, breaking one-time semantics and causing Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: common/src/main/java/org/tron/common/utils/ByteArrayMap.java::hashCode
- Entrypoint: /wallet/* public HTTP APIs
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for alternate encodings, restart windows, or map-key normalization that let one logical identifier be reused.
- Invariant to test: Replay-protection identifiers and security-critical digests must map one-to-one to one completed action.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Replay the same logical identifier through every supported encoding and public surface around /wallet/* public HTTP APIs; assert the first accepted use closes all equivalent forms.
