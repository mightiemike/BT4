# Q867: Fp2: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp2.dbl` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` — where the attacker triggers Fp2.dbl error/exception path that serializes private/spending key material into a response or log — to break the invariant that Fp2.dbl never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java` -> `Fp2.dbl`
- Entrypoint: force an error in Fp2.dbl
- Attacker controls: request/transaction/contract inputs to `Fp2.dbl` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Fp2.dbl error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Fp2.dbl never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
