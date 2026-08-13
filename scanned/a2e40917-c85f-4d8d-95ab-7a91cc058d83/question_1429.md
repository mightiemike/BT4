# Q1429: Unauthorized key export through Export

## Question
Can an unprivileged attacker use key IDs, addresses, import blobs, and current auth context at `POST /v2/keys/ocr2/export/:ID` so `Export` exports, reveals, or derives protected key material for the wrong caller, leading to unauthorized access to blockchain keys or export artifacts and violating key import/export/delete actions must stay bound to the intended role, chain, and key identifier?

## Target
- File/function: core/web/ocr2_keys_controller.go::Export
- Entrypoint: POST /v2/keys/ocr2/export/:ID
- Attacker controls: key IDs, addresses, import blobs, and current auth context
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: key import/export/delete actions must stay bound to the intended role, chain, and key identifier
- Expected Immunefi impact: unauthorized access to blockchain keys or export artifacts
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
