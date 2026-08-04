# Q2750: context-binding failure in Hash.encodeElement

## Question
Can an unprivileged attacker make shielded transaction build -> sign -> /wallet/broadcasttransaction feed crypto/src/main/java/org/tron/common/crypto/Hash.java::encodeElement data that verifies under one context but is later consumed under another, so a signature, hash, or derived identifier authorizes the wrong action and leads to Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/Hash.java::encodeElement
- Entrypoint: shielded transaction build -> sign -> /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Look for missing domain-separation inputs, omitted length or type fields, and helper functions reused across incompatible contexts.
- Invariant to test: Every security-critical signature, hash, or derived identifier must be bound to one exact context, action, and owner.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Construct pairs of requests that differ only in omitted context fields via shielded transaction build -> sign -> /wallet/broadcasttransaction; assert no proof, digest, or signature result migrates across contexts.
