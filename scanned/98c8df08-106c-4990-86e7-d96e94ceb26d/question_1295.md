# Q1295: Fp: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker triggers Fp.squared error/exception path that serializes private/spending key material into a response or log — to break the invariant that Fp.squared never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.squared`
- Entrypoint: force an error in Fp.squared
- Attacker controls: request/transaction/contract inputs to `Fp.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Fp.squared error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Fp.squared never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
