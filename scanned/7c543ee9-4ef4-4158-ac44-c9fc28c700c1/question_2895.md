# Q2895: serialization collision in ECKeyFactory.getInstance

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance serializes two distinct logical inputs to the same byte string, digest, or map key, collapsing object identity and causing Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Target concatenation boundaries, leading-zero normalization, length omission, and mixed signed/unsigned conversions.
- Invariant to test: Serialization and hashing for security-critical inputs must be injective over the attacker-controlled domain.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Generate candidate collisions through /jsonrpc eth_sendRawTransaction; assert all byte encodings, digests, and map keys remain unique across distinct logical objects.
