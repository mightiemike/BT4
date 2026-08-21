# Q225: Fp: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Fp.mul` in `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` — where the attacker triggers Fp.mul error/exception path that serializes private/spending key material into a response or log — to break the invariant that Fp.mul never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java` -> `Fp.mul`
- Entrypoint: force an error in Fp.mul
- Attacker controls: request/transaction/contract inputs to `Fp.mul` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Fp.mul error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Fp.mul never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
