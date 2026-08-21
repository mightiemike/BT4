# Q976: IncrementalMerkleVoucherContainer: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `IncrementalMerkleVoucherContainer.append` in `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleVoucherContainer.java` — where the attacker submits a shielded proof or note to IncrementalMerkleVoucherContainer.append with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that IncrementalMerkleVoucherContainer.append binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleVoucherContainer.java` -> `IncrementalMerkleVoucherContainer.append`
- Entrypoint: shielded transaction reaching IncrementalMerkleVoucherContainer.append
- Attacker controls: request/transaction/contract inputs to `IncrementalMerkleVoucherContainer.append` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to IncrementalMerkleVoucherContainer.append with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: IncrementalMerkleVoucherContainer.append binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
