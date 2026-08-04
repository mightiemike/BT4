# Q3433: raw-input canonicalization in JsonFormat.unsignedToString

## Question
Can an unprivileged attacker submit ambiguous public input through /wallet/* public HTTP APIs so framework/src/main/java/org/tron/core/services/http/JsonFormat.java::unsignedToString decodes one account, contract, or payload for validation but a different one for execution or broadcast, resulting in Execution or state selection against the wrong account or contract context?

## Target
- File/function: framework/src/main/java/org/tron/core/services/http/JsonFormat.java::unsignedToString
- Entrypoint: /wallet/* public HTTP APIs
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Target visible/base58/hex decoding, JSON-RPC data/input handling, raw-hex parsing, and any alternate public encoding of the same request.
- Invariant to test: All public APIs must canonicalize and validate one unique payload before any later execution or broadcast path consumes it.
- Expected Immunefi impact: Execution or state selection against the wrong account or contract context
- Fast validation: Replay the same logical request across all accepted encodings via /wallet/* public HTTP APIs; assert the resulting unsigned/signed tx bytes and selected objects are identical.
