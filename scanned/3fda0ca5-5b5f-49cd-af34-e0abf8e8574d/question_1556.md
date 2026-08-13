# Q1556: Low-privilege path leaking sensitive key state in Import

## Question
Can an unprivileged attacker use overlapping session and token credentials on key routes at `POST /v2/keys/vrf/import` so `Import` reveals enough protected key metadata or artifacts to reach unauthorized access to blockchain keys or export artifacts, violating key import/export/delete actions must stay bound to the intended role, chain, and key identifier?

## Target
- File/function: core/web/vrf_keys_controller.go::Import
- Entrypoint: POST /v2/keys/vrf/import
- Attacker controls: overlapping session and token credentials on key routes
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: key import/export/delete actions must stay bound to the intended role, chain, and key identifier
- Expected Immunefi impact: unauthorized access to blockchain keys or export artifacts
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
