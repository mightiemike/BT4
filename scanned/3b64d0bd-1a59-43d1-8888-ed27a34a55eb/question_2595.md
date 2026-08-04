# Q2595: serialization collision in ByteArray.toHexString

## Question
Can an unprivileged attacker use /wallet/* public HTTP APIs so common/src/main/java/org/tron/common/utils/ByteArray.java::toHexString serializes two distinct logical inputs to the same byte string, digest, or map key, collapsing object identity and causing Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: common/src/main/java/org/tron/common/utils/ByteArray.java::toHexString
- Entrypoint: /wallet/* public HTTP APIs
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Target concatenation boundaries, leading-zero normalization, length omission, and mixed signed/unsigned conversions.
- Invariant to test: Serialization and hashing for security-critical inputs must be injective over the attacker-controlled domain.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Generate candidate collisions through /wallet/* public HTTP APIs; assert all byte encodings, digests, and map keys remain unique across distinct logical objects.
