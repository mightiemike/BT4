# Q1238: context-binding failure in MerkleContainer.createInstance

## Question
Can an unprivileged attacker use /wallet/createshieldedcontractparameters so chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java::createInstance verifies a signature, proof, or derivation against the wrong context, amount, contract, or owner, letting the wrong actor authorize a spend and leading to Unauthorized shielded spend or note theft?

## Target
- File/function: chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java::createInstance
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Stress domain-separation inputs, chain or contract identifiers, amount fields, and any split between the signed/proven data and the executed data.
- Invariant to test: Every signature, proof, or derivation result must be bound to one exact context, value, and owner before it can authorize state change.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Construct pairs of requests that share a proof/signature under one context but differ under another via /wallet/createshieldedcontractparameters; assert the proof cannot migrate across contexts.
