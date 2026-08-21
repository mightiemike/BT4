# Q2827: Credentials: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Credentials.getSignInterface` in `crypto/src/main/java/org/tron/keystore/Credentials.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Credentials.getSignInterface accepts, enabling replay or weight double-count — to break the invariant that Credentials.getSignInterface rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Credentials.java` -> `Credentials.getSignInterface`
- Entrypoint: transaction/precompile path invoking Credentials.getSignInterface
- Attacker controls: request/transaction/contract inputs to `Credentials.getSignInterface` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Credentials.getSignInterface accepts, enabling replay or weight double-count
- Invariant to test: Credentials.getSignInterface rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
