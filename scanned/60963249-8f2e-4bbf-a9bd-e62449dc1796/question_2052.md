# Q2052: Bridge SSRF or local-file reachability in Show

## Question
Can an unprivileged attacker control bridge URL, bridge name, payment floor, and adapter metadata so `Show` drives the node to localhost, metadata services, or local-file-like targets, leading to retrieve sensitive data/files from a running server such as database passwords and blockchain keys and violating adapter output and caches must not let one attacker-controlled request influence another job or principal?

## Target
- File/function: core/web/bridge_types_controller.go::Show
- Entrypoint: GET /v2/bridge_types/:BridgeName
- Attacker controls: bridge URL, bridge name, payment floor, and adapter metadata
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: adapter output and caches must not let one attacker-controlled request influence another job or principal
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
