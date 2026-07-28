# Q2197: EVM already-executed check - value encoding refund misresolution

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `IsAlreadyExecuted` remain safe if they control recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call, or can that make it push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: fuzz asset address emptiness, tx type, revert message, and payload shape, then check whether the chosen vault function ever diverges from the intended mode
