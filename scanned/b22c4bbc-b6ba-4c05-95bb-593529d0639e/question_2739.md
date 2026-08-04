# Q2739: serialization collision in ECKey.signHash

## Question
Can an unprivileged attacker use /wallet/gettriggerinputforshieldedtrc20contract so crypto/src/main/java/org/tron/common/crypto/ECKey.java::signHash serializes two distinct logical inputs to the same byte string, digest, or map key, collapsing object identity and causing Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/ECKey.java::signHash
- Entrypoint: /wallet/gettriggerinputforshieldedtrc20contract
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Target concatenation boundaries, leading-zero normalization, length omission, and mixed signed/unsigned conversions.
- Invariant to test: Serialization and hashing for security-critical inputs must be injective over the attacker-controlled domain.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Generate candidate collisions through /wallet/gettriggerinputforshieldedtrc20contract; assert all byte encodings, digests, and map keys remain unique across distinct logical objects.
