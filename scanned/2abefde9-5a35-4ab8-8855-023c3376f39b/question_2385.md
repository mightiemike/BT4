# Q2385: EVM already-executed check - hash identity mode confusion

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `IsAlreadyExecuted` remain safe if they control `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row, or can that make it switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, violate the rule that the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: the `SigningHash` always commits to the exact destination transaction bytes that will be broadcast
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
