# Q1257: LibrustzcashParam: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `LibrustzcashParam.valid32Params` in `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` — where the attacker submits a shielded proof or note to LibrustzcashParam.valid32Params with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that LibrustzcashParam.valid32Params binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java` -> `LibrustzcashParam.valid32Params`
- Entrypoint: shielded transaction reaching LibrustzcashParam.valid32Params
- Attacker controls: request/transaction/contract inputs to `LibrustzcashParam.valid32Params` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to LibrustzcashParam.valid32Params with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: LibrustzcashParam.valid32Params binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
