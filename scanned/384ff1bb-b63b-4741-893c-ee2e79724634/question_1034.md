# Q1034: WalletUtils: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.warnIfSymbolicLink` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker collects signatures from WalletUtils.warnIfSymbolicLink to detect a reused or biased k allowing key recovery — to break the invariant that WalletUtils.warnIfSymbolicLink uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.warnIfSymbolicLink`
- Entrypoint: collect signatures produced via WalletUtils.warnIfSymbolicLink
- Attacker controls: request/transaction/contract inputs to `WalletUtils.warnIfSymbolicLink` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from WalletUtils.warnIfSymbolicLink to detect a reused or biased k allowing key recovery
- Invariant to test: WalletUtils.warnIfSymbolicLink uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
