# Q1440: Boundary preservation edge case in Index #3

## Question
Can an unprivileged attacker use concurrent create/delete/export requests on the same key at `GET /v2/keys/ocr2` so `Index` reaches a concrete path to unauthorized access to blockchain keys or export artifacts by breaking the invariant that key import/export/delete actions must stay bound to the intended role, chain, and key identifier, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/ocr2_keys_controller.go::Index
- Entrypoint: GET /v2/keys/ocr2
- Attacker controls: concurrent create/delete/export requests on the same key
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: key import/export/delete actions must stay bound to the intended role, chain, and key identifier
- Expected Immunefi impact: unauthorized access to blockchain keys or export artifacts
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
