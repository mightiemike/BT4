# Q1821: EVM already-executed check - function choice refund misresolution

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `IsAlreadyExecuted` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome, violate the rule that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
