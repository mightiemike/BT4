# Q1890: WalletUtils: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.generateWalletFile` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker triggers WalletUtils.generateWalletFile error/exception path that serializes private/spending key material into a response or log — to break the invariant that WalletUtils.generateWalletFile never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.generateWalletFile`
- Entrypoint: force an error in WalletUtils.generateWalletFile
- Attacker controls: request/transaction/contract inputs to `WalletUtils.generateWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers WalletUtils.generateWalletFile error/exception path that serializes private/spending key material into a response or log
- Invariant to test: WalletUtils.generateWalletFile never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
