# Q1042: WalletUtils: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.warnIfSymbolicLink` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker triggers WalletUtils.warnIfSymbolicLink error/exception path that serializes private/spending key material into a response or log — to break the invariant that WalletUtils.warnIfSymbolicLink never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.warnIfSymbolicLink`
- Entrypoint: force an error in WalletUtils.warnIfSymbolicLink
- Attacker controls: request/transaction/contract inputs to `WalletUtils.warnIfSymbolicLink` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers WalletUtils.warnIfSymbolicLink error/exception path that serializes private/spending key material into a response or log
- Invariant to test: WalletUtils.warnIfSymbolicLink never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
