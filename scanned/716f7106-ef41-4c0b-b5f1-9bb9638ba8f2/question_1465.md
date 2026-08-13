# Q1465: Boundary preservation edge case in Export #4

## Question
Can an unprivileged attacker use overlapping session and token credentials on key routes at `POST /v2/keys/ocr/export/:ID` so `Export` reaches a concrete path to authentication bypass into privileged key-management actions by breaking the invariant that no low-privilege path may reveal exportable key material or equivalent recovery artifacts, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/ocr_keys_controller.go::Export
- Entrypoint: POST /v2/keys/ocr/export/:ID
- Attacker controls: overlapping session and token credentials on key routes
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: no low-privilege path may reveal exportable key material or equivalent recovery artifacts
- Expected Immunefi impact: authentication bypass into privileged key-management actions
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
