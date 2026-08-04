# Q3906: canonical-byte collision in ZenTransactionBuilder.addOutput

## Question
Can an unprivileged attacker use /wallet/createshieldedcontractparameters to make framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java::addOutput treat two byte-distinct commitments, nullifiers, proofs, or addresses as the same object, overwriting or reusing the nullifier or anchor state/shielded note value, transparent balances, or note-spent status and causing Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java::addOutput
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Exercise leading zeros, sign extension, alternate serialization forms, and concatenation boundaries in every canonical byte path.
- Invariant to test: Canonical serialization must be injective for every security-critical identifier.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Fuzz alternate serializations for the same logical fields via /wallet/createshieldedcontractparameters; assert stored keys, hashes, and lookups never collide across distinct objects.
