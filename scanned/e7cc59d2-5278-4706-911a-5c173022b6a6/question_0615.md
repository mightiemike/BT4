# Q615: Wallet: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createWalletFile` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker collects signatures from Wallet.createWalletFile to detect a reused or biased k allowing key recovery — to break the invariant that Wallet.createWalletFile uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createWalletFile`
- Entrypoint: collect signatures produced via Wallet.createWalletFile
- Attacker controls: request/transaction/contract inputs to `Wallet.createWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Wallet.createWalletFile to detect a reused or biased k allowing key recovery
- Invariant to test: Wallet.createWalletFile uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
