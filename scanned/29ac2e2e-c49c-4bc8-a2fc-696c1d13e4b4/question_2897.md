# Q2897: BN128Fp: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.create` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker triggers BN128Fp.create error/exception path that serializes private/spending key material into a response or log — to break the invariant that BN128Fp.create never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.create`
- Entrypoint: force an error in BN128Fp.create
- Attacker controls: request/transaction/contract inputs to `BN128Fp.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers BN128Fp.create error/exception path that serializes private/spending key material into a response or log
- Invariant to test: BN128Fp.create never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
