# Q3903: PairingCheck: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.millerLoop` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker manipulates the recovery byte so PairingCheck.millerLoop recovers an unintended address the attacker can predict — to break the invariant that PairingCheck.millerLoop recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.millerLoop`
- Entrypoint: path calling PairingCheck.millerLoop with crafted v
- Attacker controls: request/transaction/contract inputs to `PairingCheck.millerLoop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so PairingCheck.millerLoop recovers an unintended address the attacker can predict
- Invariant to test: PairingCheck.millerLoop recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
