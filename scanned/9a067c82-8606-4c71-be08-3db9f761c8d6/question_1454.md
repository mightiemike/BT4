# Q1454: Boundary preservation edge case in Delete #1

## Question
Can an unprivileged attacker use key IDs, addresses, import blobs, and current auth context at `DELETE /v2/keys/ocr/:keyID` so `Delete` reaches a concrete path to authentication bypass into privileged key-management actions by breaking the invariant that no low-privilege path may reveal exportable key material or equivalent recovery artifacts, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/ocr_keys_controller.go::Delete
- Entrypoint: DELETE /v2/keys/ocr/:keyID
- Attacker controls: key IDs, addresses, import blobs, and current auth context
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: no low-privilege path may reveal exportable key material or equivalent recovery artifacts
- Expected Immunefi impact: authentication bypass into privileged key-management actions
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
