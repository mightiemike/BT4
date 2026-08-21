# Q2913: WalletUtils: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.warnIfSymbolicLink` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker submits a non-canonical (high-s) or over-length signature that WalletUtils.warnIfSymbolicLink accepts, enabling replay or weight double-count — to break the invariant that WalletUtils.warnIfSymbolicLink rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.warnIfSymbolicLink`
- Entrypoint: transaction/precompile path invoking WalletUtils.warnIfSymbolicLink
- Attacker controls: request/transaction/contract inputs to `WalletUtils.warnIfSymbolicLink` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that WalletUtils.warnIfSymbolicLink accepts, enabling replay or weight double-count
- Invariant to test: WalletUtils.warnIfSymbolicLink rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
