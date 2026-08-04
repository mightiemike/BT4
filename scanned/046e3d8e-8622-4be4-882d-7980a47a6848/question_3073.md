# Q3073: nullifier-uniqueness bypass in Fp6.hashCode

## Question
Can an unprivileged attacker reach /jsonrpc eth_sendRawTransaction with crafted shielded inputs so crypto/src/main/java/org/tron/common/crypto/zksnark/Fp6.java::hashCode accepts one logical spend identifier more than once, breaks one-time semantics between the canonical byte representation or derived key/address and the intended owner, transaction context, or verification result, and causes Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/Fp6.java::hashCode
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for alternate encodings, context changes, or timing windows that let the same spend-like identifier bypass uniqueness checks.
- Invariant to test: One shielded spend, proof, or spend-like identifier must be accepted exactly once network-wide.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Generate equivalent shielded spends or identifiers through /jsonrpc eth_sendRawTransaction; assert every equivalent form maps to one spent object and the second attempt fails.
