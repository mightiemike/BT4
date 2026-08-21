# Q2990: WalletUtils: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.loadCredentials` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker manipulates the recovery byte so WalletUtils.loadCredentials recovers an unintended address the attacker can predict — to break the invariant that WalletUtils.loadCredentials recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.loadCredentials`
- Entrypoint: path calling WalletUtils.loadCredentials with crafted v
- Attacker controls: request/transaction/contract inputs to `WalletUtils.loadCredentials` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so WalletUtils.loadCredentials recovers an unintended address the attacker can predict
- Invariant to test: WalletUtils.loadCredentials recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
