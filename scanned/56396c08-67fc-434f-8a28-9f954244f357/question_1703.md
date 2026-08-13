# Q1703: Boundary preservation edge case in CreateEVMLegacy #4

## Question
Can an unprivileged attacker use numeric parsing and chain-binding edge cases on admin run routes at `POST /v2/transfers/evm` so `CreateEVMLegacy` reaches a concrete path to authentication bypass into privileged run or transfer actions by breaking the invariant that amount, chain, and relayer validation must not diverge from the transaction actually submitted, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/evm_transfer_controller.go::CreateEVMLegacy
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: numeric parsing and chain-binding edge cases on admin run routes
- Exploit idea: Probe chain/sender/amount binding and repeated transfer/replay operations to verify whether the node can be tricked into wrong-target or duplicate side effects.
- Invariant to test: amount, chain, and relayer validation must not diverge from the transaction actually submitted
- Expected Immunefi impact: authentication bypass into privileged run or transfer actions
- Fast validation: Run integration tests around transfer/replay/forwarder operations with boundary amounts and wrong chain/relayer state; assert no cross-target or duplicate effect occurs.
