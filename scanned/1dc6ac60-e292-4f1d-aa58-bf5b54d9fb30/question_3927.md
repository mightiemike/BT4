# Q3927: SVM signing hash build - account material mode switch after sign

## Question
When an unprivileged actor trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message, does `GetOutboundSigningRequest` remain safe if they control accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes, or can that make it change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast, violate the rule that signing, broadcast, and resolution all agree on one execution mode for the outbound, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: trigger a public Solana revert outbound with attacker-controlled refund recipient and revert message
- Attacker controls: accounts, ixData, ALT lookups, stored ix-data PDAs, and revert fields derived from attacker-controlled payload bytes
- Exploit idea: change execute, withdraw, revert, rescue, native, or SPL semantics between signing and final broadcast
- Invariant to test: signing, broadcast, and resolution all agree on one execution mode for the outbound
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: vary ID lengths, leading zeros, and payload formats and verify distinct outbounds cannot collapse to the same signed meaning
