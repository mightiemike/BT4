# Q2803: query-execution encoding mismatch in SignatureInterface.class-level path

## Question
Can an unprivileged attacker abuse gRPC broadcastTransaction so crypto/src/main/java/org/tron/common/crypto/SignatureInterface.java::class-level path returns one canonical object on the query/build path but a different byte form is later consumed by execution or broadcast, letting the mismatch reach Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/SignatureInterface.java::class-level path
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Compare address and byte formatting helpers used in displayed results, built transactions, and final execution paths.
- Invariant to test: The object shown to the user and the bytes later executed against must be derived from one identical canonical representation.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Chain the relevant read/build path and the later execution path around gRPC broadcastTransaction; assert they agree on the same canonical bytes and selected object.
