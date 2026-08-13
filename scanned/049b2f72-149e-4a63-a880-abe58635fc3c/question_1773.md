# Q1773: Bridge cache poisoning in MarshalBridgeMetaData

## Question
Can an unprivileged attacker use external-initiator name, URL, and generated auth-token pairing so `MarshalBridgeMetaData` stores or retrieves adapter output under a cache key later consumed by a different job or principal, causing misreporting of prices and/or data and breaking outbound fetches must stay confined away from local files and sensitive internal endpoints?

## Target
- File/function: core/bridges/bridge_type.go::MarshalBridgeMetaData
- Entrypoint: POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node
- Attacker controls: external-initiator name, URL, and generated auth-token pairing
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: outbound fetches must stay confined away from local files and sensitive internal endpoints
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
