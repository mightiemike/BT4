# Q500: WalletUtils: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.generateWalletFile` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker collects signatures from WalletUtils.generateWalletFile to detect a reused or biased k allowing key recovery — to break the invariant that WalletUtils.generateWalletFile uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.generateWalletFile`
- Entrypoint: collect signatures produced via WalletUtils.generateWalletFile
- Attacker controls: request/transaction/contract inputs to `WalletUtils.generateWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from WalletUtils.generateWalletFile to detect a reused or biased k allowing key recovery
- Invariant to test: WalletUtils.generateWalletFile uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
