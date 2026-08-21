# Q226: BN128: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.toEthNotation` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker submits a non-canonical (high-s) or over-length signature that BN128.toEthNotation accepts, enabling replay or weight double-count — to break the invariant that BN128.toEthNotation rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.toEthNotation`
- Entrypoint: transaction/precompile path invoking BN128.toEthNotation
- Attacker controls: request/transaction/contract inputs to `BN128.toEthNotation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that BN128.toEthNotation accepts, enabling replay or weight double-count
- Invariant to test: BN128.toEthNotation rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
