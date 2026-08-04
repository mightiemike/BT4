# Q2945: truncation-boundary bug in SM2.signatureToKeyBytes

## Question
Can an unprivileged attacker send boundary-length inputs through /wallet/scanshieldedtrc20notesbyivk so crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java::signatureToKeyBytes truncates, extends, or reinterprets bytes in a way that changes the selected owner, amount, or target object and results in Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java::signatureToKeyBytes
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Exercise empty values, exact threshold lengths, oversize inputs, signed/unsigned edges, and zero-padding boundaries.
- Invariant to test: Boundary handling must either reject malformed lengths or preserve the exact intended value without reinterpretation.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz every length-sensitive field that reaches crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java::signatureToKeyBytes through /wallet/scanshieldedtrc20notesbyivk; assert canonical decode and exact round-trip behavior.
