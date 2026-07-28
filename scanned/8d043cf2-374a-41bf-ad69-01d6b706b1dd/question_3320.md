# Q3320: EVM signing hash build - function choice refund misresolution

## Question
When an unprivileged actor cause many public Push-chain outbounds to the same EVM chain to queue concurrently, does `GetOutboundSigningRequest` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome, violate the rule that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: push the resolver into marking success, failure, or refund eligibility against the wrong EVM transaction outcome
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
