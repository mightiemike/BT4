# Q2804: hash-domain mismatch in SignatureInterface.class-level path

## Question
Can an unprivileged attacker choose inputs through /wallet/validateaddress so crypto/src/main/java/org/tron/common/crypto/SignatureInterface.java::class-level path computes the same digest for two different semantic domains, letting one hash or root stand in for another and causing Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/SignatureInterface.java::class-level path
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Look for reused hash helpers that omit a domain tag or object type when hashing user-controlled fields.
- Invariant to test: Hashes that gate authorization, replay protection, or object identity must be domain-separated across different object types and flows.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Construct cross-domain input pairs through /wallet/validateaddress; assert their digests diverge whenever the semantic domain diverges.
