# Q3017: HttpSelfFormatFieldName: hex/base58 dual-accept

## Question
Can an unprivileged attacker (HTTP servlet) abuse `HttpSelfFormatFieldName.isNameStringFormat` in `framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java` — where the attacker sends an address/param to HttpSelfFormatFieldName.isNameStringFormat in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner — to break the invariant that HttpSelfFormatFieldName.isNameStringFormat and downstream decode the same bytes to the same address, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java` -> `HttpSelfFormatFieldName.isNameStringFormat`
- Entrypoint: HTTP request to HttpSelfFormatFieldName.isNameStringFormat with dual-form address
- Attacker controls: request/transaction/contract inputs to `HttpSelfFormatFieldName.isNameStringFormat` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends an address/param to HttpSelfFormatFieldName.isNameStringFormat in a form the servlet accepts but the actuator re-decodes differently, resolving a different owner
- Invariant to test: HttpSelfFormatFieldName.isNameStringFormat and downstream decode the same bytes to the same address
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: differential test hex vs base58 for the same field
