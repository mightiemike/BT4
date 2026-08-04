# Q2774: context-binding failure in SignInterface.class-level path

## Question
Can an unprivileged attacker make /wallet/validateaddress feed crypto/src/main/java/org/tron/common/crypto/SignInterface.java::class-level path data that verifies under one context but is later consumed under another, so a signature, hash, or derived identifier authorizes the wrong action and leads to Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/SignInterface.java::class-level path
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Look for missing domain-separation inputs, omitted length or type fields, and helper functions reused across incompatible contexts.
- Invariant to test: Every security-critical signature, hash, or derived identifier must be bound to one exact context, action, and owner.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Construct pairs of requests that differ only in omitted context fields via /wallet/validateaddress; assert no proof, digest, or signature result migrates across contexts.
