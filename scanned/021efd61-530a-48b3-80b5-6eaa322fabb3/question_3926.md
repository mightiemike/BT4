# Q3926: Proof-shape differential in SetupConfigInfo

## Question
Can an unprivileged attacker craft token amounts, fee fields, gas metadata, and nonce values so `SetupConfigInfo` validates Merkle-proof structure under one assumption while downstream hashing/execution uses another, leading to RMN onchain curse bypass and violating commit and execute decoding must preserve the exact message set and chain identity?

## Target
- File/function: core/capabilities/ccip/ccip_integration_tests/integrationhelpers/integration_helpers.go::SetupConfigInfo
- Entrypoint: crafted onchain CCIP message, report, proof, or plugin config consumed by the node
- Attacker controls: token amounts, fee fields, gas metadata, and nonce values
- Exploit idea: Round-trip adversarial reports/proofs/configs through the exact codec/validator and confirm that chain, message, nonce, and fee semantics remain identical end to end.
- Invariant to test: commit and execute decoding must preserve the exact message set and chain identity
- Expected Immunefi impact: RMN onchain curse bypass
- Fast validation: Fuzz encode/decode and proof-validation with adversarial vectors; assert message set, chain selector, nonce, and accounting stay identical before acceptance.
