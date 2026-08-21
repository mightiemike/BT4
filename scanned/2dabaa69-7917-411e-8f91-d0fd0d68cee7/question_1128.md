# Q1128: PairingCheck: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.flippedMillerLoopDoubling` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker submits a signature/pubkey to PairingCheck.flippedMillerLoopDoubling that is short or padded but still parsed, recovering a shifted value — to break the invariant that PairingCheck.flippedMillerLoopDoubling enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.flippedMillerLoopDoubling`
- Entrypoint: precompile/verify path to PairingCheck.flippedMillerLoopDoubling
- Attacker controls: request/transaction/contract inputs to `PairingCheck.flippedMillerLoopDoubling` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to PairingCheck.flippedMillerLoopDoubling that is short or padded but still parsed, recovering a shifted value
- Invariant to test: PairingCheck.flippedMillerLoopDoubling enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
