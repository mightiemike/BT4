# Q3422: BN128Fp: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker triggers BN128Fp.zero error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128Fp.zero never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.zero`
- Entrypoint: force an error in BN128Fp.zero
- Attacker controls: request/transaction/contract inputs to `BN128Fp.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128Fp.zero error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128Fp.zero never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
