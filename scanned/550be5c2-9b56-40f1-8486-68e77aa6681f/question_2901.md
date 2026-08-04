# Q2901: map-key aliasing in ECKeyFactory.getInstance

## Question
Can an unprivileged attacker feed /wallet/scanshieldedtrc20notesbyivk values that make crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance alias two distinct keys in a byte-array map, set, or cache, so the wrong record is overwritten or reused and the outcome becomes Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Stress custom equality, hashCode, prefix handling, and mutable byte-array wrappers that may not preserve key identity.
- Invariant to test: Distinct attacker-controlled identifiers must remain distinct as keys in every security-relevant collection or cache.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Fuzz collection-key lifecycles through /wallet/scanshieldedtrc20notesbyivk; assert insert/get/remove operations never cross-contaminate distinct logical keys.
