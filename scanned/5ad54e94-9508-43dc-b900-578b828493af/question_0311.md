# Q311: Fp2: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.squared` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker triggers Fp2.squared error/exception path that serializes private/spending key material into a response or log — to break the invariant that Fp2.squared never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.squared`
- Entrypoint: force an error in Fp2.squared
- Attacker controls: request/transaction/contract inputs to `Fp2.squared` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Fp2.squared error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Fp2.squared never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
