# Q3917: Boundary preservation edge case in SetupConfigInfo #3

## Question
Can an unprivileged attacker use source/destination chain selectors and address encodings at `crafted onchain CCIP message, report, proof, or plugin config consumed by the node` so `SetupConfigInfo` reaches a concrete path to RMN onchain curse bypass by breaking the invariant that commit and execute decoding must preserve the exact message set and chain identity, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/capabilities/ccip/ccip_integration_tests/integrationhelpers/integration_helpers.go::SetupConfigInfo
- Entrypoint: crafted onchain CCIP message, report, proof, or plugin config consumed by the node
- Attacker controls: source/destination chain selectors and address encodings
- Exploit idea: Round-trip adversarial reports/proofs/configs through the exact codec/validator and confirm that chain, message, nonce, and fee semantics remain identical end to end.
- Invariant to test: commit and execute decoding must preserve the exact message set and chain identity
- Expected Immunefi impact: RMN onchain curse bypass
- Fast validation: Fuzz encode/decode and proof-validation with adversarial vectors; assert message set, chain selector, nonce, and accounting stay identical before acceptance.
