# Q1491: Key import or binding confusion in Create

## Question
Can an unprivileged attacker exploit chain-selection parameters and key-address binding at `POST /v2/keys/p2p` so `Create` imports or binds key material to the wrong chain, role, or address context, causing authentication bypass into privileged key-management actions and breaking no low-privilege path may reveal exportable key material or equivalent recovery artifacts?

## Target
- File/function: core/web/p2p_keys_controller.go::Create
- Entrypoint: POST /v2/keys/p2p
- Attacker controls: chain-selection parameters and key-address binding
- Exploit idea: Exercise import/export/delete/create against the real controller and keystore boundary to prove whether protected key state leaks or mutates across roles/chains.
- Invariant to test: no low-privilege path may reveal exportable key material or equivalent recovery artifacts
- Expected Immunefi impact: authentication bypass into privileged key-management actions
- Fast validation: Exercise the controller plus keystore with crafted IDs/import blobs and mixed auth; assert no unauthorized export, delete, or chain rebinding occurs.
