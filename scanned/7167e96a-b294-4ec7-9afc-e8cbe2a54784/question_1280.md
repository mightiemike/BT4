# Q1280: Wallet: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.generateAes128CtrDerivedKey` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker triggers Wallet.generateAes128CtrDerivedKey error/exception path that serializes private/spending key material into a response or log — to break the invariant that Wallet.generateAes128CtrDerivedKey never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.generateAes128CtrDerivedKey`
- Entrypoint: force an error in Wallet.generateAes128CtrDerivedKey
- Attacker controls: request/transaction/contract inputs to `Wallet.generateAes128CtrDerivedKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Wallet.generateAes128CtrDerivedKey error/exception path that serializes private/spending key material into a response or log
- Invariant to test: Wallet.generateAes128CtrDerivedKey never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
