# Q3189: PairingCheck: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.millerLoop` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker submits a signature/pubkey to PairingCheck.millerLoop that is short or padded but still parsed, recovering a shifted value — to break the invariant that PairingCheck.millerLoop enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.millerLoop`
- Entrypoint: precompile/verify path to PairingCheck.millerLoop
- Attacker controls: request/transaction/contract inputs to `PairingCheck.millerLoop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to PairingCheck.millerLoop that is short or padded but still parsed, recovering a shifted value
- Invariant to test: PairingCheck.millerLoop enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
