# Q1244: WalletUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker feeds WalletUtil.generateContractAddress a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that WalletUtil.generateContractAddress rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress`
- Entrypoint: numeric bytes into WalletUtil.generateContractAddress
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds WalletUtil.generateContractAddress a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: WalletUtil.generateContractAddress rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
