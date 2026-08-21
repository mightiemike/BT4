# Q2740: Credentials: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Credentials.getSignInterface` in `crypto/src/main/java/org/tron/keystore/Credentials.java` — where the attacker manipulates the recovery byte so Credentials.getSignInterface recovers an unintended address the attacker can predict — to break the invariant that Credentials.getSignInterface recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Credentials.java` -> `Credentials.getSignInterface`
- Entrypoint: path calling Credentials.getSignInterface with crafted v
- Attacker controls: request/transaction/contract inputs to `Credentials.getSignInterface` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Credentials.getSignInterface recovers an unintended address the attacker can predict
- Invariant to test: Credentials.getSignInterface recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
