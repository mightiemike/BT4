# Q1126: Wallet: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.create` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker triggers Wallet.create error/exception path that serializes private/spending key material into a response or log — to break the invariant that Wallet.create never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.create`
- Entrypoint: force an error in Wallet.create
- Attacker controls: request/transaction/contract inputs to `Wallet.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Wallet.create error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Wallet.create never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
