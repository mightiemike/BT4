# Q2899: Wallet: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createWalletFile` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker triggers Wallet.createWalletFile error/exception path that serializes private/spending key material into a response or log — to break the invariant that Wallet.createWalletFile never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createWalletFile`
- Entrypoint: force an error in Wallet.createWalletFile
- Attacker controls: request/transaction/contract inputs to `Wallet.createWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Wallet.createWalletFile error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Wallet.createWalletFile never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
