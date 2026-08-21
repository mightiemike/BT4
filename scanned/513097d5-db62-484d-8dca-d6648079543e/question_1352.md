# Q1352: Wallet: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createStandard` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker triggers Wallet.createStandard error/exception path that serializes private/spending key material into a response or log — to break the invariant that Wallet.createStandard never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createStandard`
- Entrypoint: force an error in Wallet.createStandard
- Attacker controls: request/transaction/contract inputs to `Wallet.createStandard` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Wallet.createStandard error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Wallet.createStandard never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
