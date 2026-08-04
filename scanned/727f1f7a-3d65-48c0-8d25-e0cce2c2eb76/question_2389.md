# Q2389: nullifier-uniqueness bypass in NullifierStore.has

## Question
Can an unprivileged attacker reach /wallet/createshieldedcontractparameters with crafted shielded inputs so chainbase/src/main/java/org/tron/core/store/NullifierStore.java::has accepts one logical spend identifier more than once, breaks one-time semantics between the nullifier or anchor state and shielded note value, transparent balances, or note-spent status, and causes Double spend of one shielded note or withdrawal?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/NullifierStore.java::has
- Entrypoint: /wallet/createshieldedcontractparameters
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Search for alternate encodings, context changes, or timing windows that let the same spend-like identifier bypass uniqueness checks.
- Invariant to test: One shielded spend, proof, or spend-like identifier must be accepted exactly once network-wide.
- Expected Immunefi impact: Double spend of one shielded note or withdrawal
- Fast validation: Generate equivalent shielded spends or identifiers through /wallet/createshieldedcontractparameters; assert every equivalent form maps to one spent object and the second attempt fails.
