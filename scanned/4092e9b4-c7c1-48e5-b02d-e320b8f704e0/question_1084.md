# Q1084: Credentials: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Credentials.getSignInterface` in `crypto/src/main/java/org/tron/keystore/Credentials.java` — where the attacker triggers Credentials.getSignInterface error/exception path that serializes private/spending key material into a response or log — to break the invariant that Credentials.getSignInterface never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Credentials.java` -> `Credentials.getSignInterface`
- Entrypoint: force an error in Credentials.getSignInterface
- Attacker controls: request/transaction/contract inputs to `Credentials.getSignInterface` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Credentials.getSignInterface error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Credentials.getSignInterface never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
