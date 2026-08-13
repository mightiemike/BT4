# Q1925: Secrets-fetch escape in CreateBridgeType

## Question
Can an unprivileged attacker use job-owned bridge names, dot IDs, and cached response slots so `CreateBridgeType` fetches attacker-shaped secret locations and reaches protected internal resources or credentials, causing retrieve sensitive data/files from a running server such as database passwords and blockchain keys and violating outbound fetches must stay confined away from local files and sensitive internal endpoints?

## Target
- File/function: core/bridges/orm.go::CreateBridgeType
- Entrypoint: POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node
- Attacker controls: job-owned bridge names, dot IDs, and cached response slots
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: outbound fetches must stay confined away from local files and sensitive internal endpoints
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
