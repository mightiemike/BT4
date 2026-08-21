# Q3387: ShieldedTRC20ParametersBuilder: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC` in `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` — where the attacker submits a shielded proof or note to ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java` -> `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC`
- Entrypoint: shielded transaction reaching ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC
- Attacker controls: request/transaction/contract inputs to `ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: ShieldedTRC20ParametersBuilder.encodeReceiveDescriptionWithoutC binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
