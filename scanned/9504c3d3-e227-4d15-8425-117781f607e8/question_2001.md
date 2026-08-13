# Q2001: Boundary preservation edge case in FindExternalInitiatorByName #5

## Question
Can an unprivileged attacker use job-owned bridge names, dot IDs, and cached response slots at `POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node` so `FindExternalInitiatorByName` reaches a concrete path to misreporting of prices and/or data by breaking the invariant that external-initiator and bridge identities must stay bound to the correct auth material and name, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/bridges/orm.go::FindExternalInitiatorByName
- Entrypoint: POST /v2/bridge_types, POST /v2/external_initiators, or public/offchain adapter input consumed by the node
- Attacker controls: job-owned bridge names, dot IDs, and cached response slots
- Exploit idea: Test localhost/metadata URLs, cache-key collisions, and external-initiator auth mismatches on the exact adapter/bridge path.
- Invariant to test: external-initiator and bridge identities must stay bound to the correct auth material and name
- Expected Immunefi impact: misreporting of prices and/or data
- Fast validation: Use local HTTP targets plus cache-collision vectors; assert no internal fetch, cross-job cache bleed, or wrong EI identity acceptance occurs.
