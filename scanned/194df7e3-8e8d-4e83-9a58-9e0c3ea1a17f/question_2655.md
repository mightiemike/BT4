# Q2655: WalletUtils: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.writeWalletFile` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker collects signatures from WalletUtils.writeWalletFile to detect a reused or biased k allowing key recovery — to break the invariant that WalletUtils.writeWalletFile uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.writeWalletFile`
- Entrypoint: collect signatures produced via WalletUtils.writeWalletFile
- Attacker controls: request/transaction/contract inputs to `WalletUtils.writeWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from WalletUtils.writeWalletFile to detect a reused or biased k allowing key recovery
- Invariant to test: WalletUtils.writeWalletFile uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
