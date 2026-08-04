# Q1141: owner-binding bypass in Commons.decode58Check

## Question
Can an unprivileged attacker enter through gRPC broadcastTransaction with crafted ownership fields and permission metadata so chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check binds authorization to the wrong account, mutates transaction-processing state and the resulting accounting, receipt, or index state on behalf of a victim, and leads to Unauthorized transaction execution or state mutation?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Try to make ownership resolution, permission selection, or caller binding point at a victim while the rest of the payload stays attacker-controlled.
- Invariant to test: Only the signer set that satisfies the required permission should be able to change transaction-processing state or the resulting accounting, receipt, or index state.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Create attacker and victim accounts, fuzz ownership and permission fields through gRPC broadcastTransaction, and assert victim-side transaction-processing state/the resulting accounting, receipt, or index state never change without victim signatures.
