# Q1167: Wallet: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createLight` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker manipulates the recovery byte so Wallet.createLight recovers an unintended address the attacker can predict — to break the invariant that Wallet.createLight recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createLight`
- Entrypoint: path calling Wallet.createLight with crafted v
- Attacker controls: request/transaction/contract inputs to `Wallet.createLight` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Wallet.createLight recovers an unintended address the attacker can predict
- Invariant to test: Wallet.createLight recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
