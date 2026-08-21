# Q1934: JLibrustzcash: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashComputeNf` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker submits a shielded proof or note to JLibrustzcash.librustzcashComputeNf with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that JLibrustzcash.librustzcashComputeNf binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashComputeNf`
- Entrypoint: shielded transaction reaching JLibrustzcash.librustzcashComputeNf
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashComputeNf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to JLibrustzcash.librustzcashComputeNf with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: JLibrustzcash.librustzcashComputeNf binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
