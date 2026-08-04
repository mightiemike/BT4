# Q2849: truncation-boundary bug in Keccak512.copy

## Question
Can an unprivileged attacker send boundary-length inputs through /jsonrpc so crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak512.java::copy truncates, extends, or reinterprets bytes in a way that changes the selected owner, amount, or target object and results in Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak512.java::copy
- Entrypoint: /jsonrpc
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Exercise empty values, exact threshold lengths, oversize inputs, signed/unsigned edges, and zero-padding boundaries.
- Invariant to test: Boundary handling must either reject malformed lengths or preserve the exact intended value without reinterpretation.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz every length-sensitive field that reaches crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak512.java::copy through /jsonrpc; assert canonical decode and exact round-trip behavior.
