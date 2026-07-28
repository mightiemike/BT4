# Q3607: EVM already-executed check - value encoding nonce collision

## Question
When an unprivileged actor cause many public Push-chain outbounds to the same EVM chain to queue concurrently, does `IsAlreadyExecuted` remain safe if they control recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
