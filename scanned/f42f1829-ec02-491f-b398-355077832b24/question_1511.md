# Q1511: Boundary preservation edge case in Import #2

## Question
Can an unprivileged attacker use chain-selection parameters and key-address binding at `POST /v2/keys/p2p/import` so `Import` reaches a concrete path to rate limit violations with real security impact by breaking the invariant that chain selection and key address binding must remain consistent end to end, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/p2p_keys_controller.go::Import
- Entrypoint: POST /v2/keys/p2p/import
- Attacker controls: chain-selection parameters and key-address binding
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: chain selection and key address binding must remain consistent end to end
- Expected Immunefi impact: rate limit violations with real security impact
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
