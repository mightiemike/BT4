# Q1506: Cross-key identifier race in Export

## Question
Can an unprivileged attacker race concurrent create/delete/export requests on the same key at `POST /v2/keys/p2p/export/:ID` so `Export` deletes, exports, or reuses a different key than the one actually authorized, leading to rate limit violations with real security impact and violating chain selection and key address binding must remain consistent end to end?

## Target
- File/function: core/web/p2p_keys_controller.go::Export
- Entrypoint: POST /v2/keys/p2p/export/:ID
- Attacker controls: concurrent create/delete/export requests on the same key
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: chain selection and key address binding must remain consistent end to end
- Expected Immunefi impact: rate limit violations with real security impact
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
