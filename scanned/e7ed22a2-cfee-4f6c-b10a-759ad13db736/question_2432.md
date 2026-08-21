# Q2432: BN128Fp: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128Fp.zero` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` — where the attacker submits a non-canonical (high-s) or over-length signature that BN128Fp.zero accepts, enabling replay or weight double-count — to break the invariant that BN128Fp.zero rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java` -> `BN128Fp.zero`
- Entrypoint: transaction/precompile path invoking BN128Fp.zero
- Attacker controls: request/transaction/contract inputs to `BN128Fp.zero` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that BN128Fp.zero accepts, enabling replay or weight double-count
- Invariant to test: BN128Fp.zero rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
