# Q82: Wallet: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createLight` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Wallet.createLight accepts, enabling replay or weight double-count — to break the invariant that Wallet.createLight rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createLight`
- Entrypoint: transaction/precompile path invoking Wallet.createLight
- Attacker controls: request/transaction/contract inputs to `Wallet.createLight` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Wallet.createLight accepts, enabling replay or weight double-count
- Invariant to test: Wallet.createLight rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
