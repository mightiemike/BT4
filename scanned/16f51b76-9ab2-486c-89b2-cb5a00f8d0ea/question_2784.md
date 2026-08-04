# Q2784: cross-surface byte mismatch in SignInterface.class-level path

## Question
Can an unprivileged attacker use /wallet/validateaddress so crypto/src/main/java/org/tron/common/crypto/SignInterface.java::class-level path produces byte output on one surface that another public surface interprets differently, turning harmless-looking input into Signature, address, or proof confusion that lets the wrong actor authorize or spend when reused elsewhere?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/SignInterface.java::class-level path
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Compare bytes emitted by helpers for JSON, HTTP, gRPC, and raw-transaction paths, especially when one surface strips or adds prefixes.
- Invariant to test: Every public surface must interpret and emit one canonical byte representation for the same logical object.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Round-trip the same logical object across all public surfaces around /wallet/validateaddress; assert every emitted byte sequence is canonical and mutually reversible.
