# Q2014: External-initiator auth confusion in UpdateBridgeType

## Question
Can an unprivileged attacker exploit adapter response bytes, cache keys, and retry/size behavior at `POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node` so `UpdateBridgeType` authenticates, persists, or resolves an external-initiator identity incorrectly, causing execute arbitrary system commands if adapter or job execution becomes attacker-controlled and violating external-initiator and bridge identities must stay bound to the correct auth material and name?

## Target
- File/function: core/bridges/orm.go::UpdateBridgeType
- Entrypoint: POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node
- Attacker controls: adapter response bytes, cache keys, and retry/size behavior
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: external-initiator and bridge identities must stay bound to the correct auth material and name
- Expected Immunefi impact: execute arbitrary system commands if adapter or job execution becomes attacker-controlled
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
