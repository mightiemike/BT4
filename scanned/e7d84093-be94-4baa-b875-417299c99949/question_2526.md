# Q2526: WalletUtils: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.stripPasswordLine` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker submits a signature/pubkey to WalletUtils.stripPasswordLine that is short or padded but still parsed, recovering a shifted value — to break the invariant that WalletUtils.stripPasswordLine enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.stripPasswordLine`
- Entrypoint: precompile/verify path to WalletUtils.stripPasswordLine
- Attacker controls: request/transaction/contract inputs to `WalletUtils.stripPasswordLine` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to WalletUtils.stripPasswordLine that is short or padded but still parsed, recovering a shifted value
- Invariant to test: WalletUtils.stripPasswordLine enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
