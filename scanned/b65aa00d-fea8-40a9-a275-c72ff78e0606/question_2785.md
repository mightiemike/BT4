# Q2785: SignUtils: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.getGeneratedRandomSign` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker triggers SignUtils.getGeneratedRandomSign error/exception path that serializes private/spending key material into a response or log — to break the invariant that SignUtils.getGeneratedRandomSign never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.getGeneratedRandomSign`
- Entrypoint: force an error in SignUtils.getGeneratedRandomSign
- Attacker controls: request/transaction/contract inputs to `SignUtils.getGeneratedRandomSign` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SignUtils.getGeneratedRandomSign error/exception path that serializes private/spending key material into a response or log
- Invariant to test: SignUtils.getGeneratedRandomSign never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
