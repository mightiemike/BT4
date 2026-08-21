# Q2819: SignUtils: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker triggers SignUtils.signatureToAddress error/exception path that serializes private/spending key material into a response or log — to break the invariant that SignUtils.signatureToAddress never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.signatureToAddress`
- Entrypoint: force an error in SignUtils.signatureToAddress
- Attacker controls: request/transaction/contract inputs to `SignUtils.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SignUtils.signatureToAddress error/exception path that serializes private/spending key material into a response or log
- Invariant to test: SignUtils.signatureToAddress never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
