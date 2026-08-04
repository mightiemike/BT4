# Q2897: truncation-boundary bug in ECKeyFactory.getInstance

## Question
Can an unprivileged attacker send boundary-length inputs through /wallet/createshieldedcontractparameters so crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance truncates, extends, or reinterprets bytes in a way that changes the selected owner, amount, or target object and results in Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Exercise empty values, exact threshold lengths, oversize inputs, signed/unsigned edges, and zero-padding boundaries.
- Invariant to test: Boundary handling must either reject malformed lengths or preserve the exact intended value without reinterpretation.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz every length-sensitive field that reaches crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance through /wallet/createshieldedcontractparameters; assert canonical decode and exact round-trip behavior.
