# Q2529: Hash: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.ripemd160` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker passes an off-curve or identity point to Hash.ripemd160 causing wrong verification or a crash — to break the invariant that Hash.ripemd160 validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.ripemd160`
- Entrypoint: precompile/verify path to Hash.ripemd160
- Attacker controls: request/transaction/contract inputs to `Hash.ripemd160` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Hash.ripemd160 causing wrong verification or a crash
- Invariant to test: Hash.ripemd160 validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
