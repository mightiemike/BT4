# Q2795: WalletUtils: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.passwordValid` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker manipulates the recovery byte so WalletUtils.passwordValid recovers an unintended address the attacker can predict — to break the invariant that WalletUtils.passwordValid recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.passwordValid`
- Entrypoint: path calling WalletUtils.passwordValid with crafted v
- Attacker controls: request/transaction/contract inputs to `WalletUtils.passwordValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so WalletUtils.passwordValid recovers an unintended address the attacker can predict
- Invariant to test: WalletUtils.passwordValid recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
