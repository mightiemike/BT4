# Q1105: GraphQL object-identity confusion in CreateVRFKey

## Question
Can an unprivileged attacker use object IDs, nested input objects, and mutation ordering through `POST /query GraphQL mutation `CreateVRFKey`` so `CreateVRFKey` authorizes one object but mutates or reads another underlying resource, causing unauthorized access to sensitive node state or secrets exposed via GraphQL and violating object identity and nested mutation inputs must stay bound to the same authorized resource?

## Target
- File/function: core/web/resolver/mutation.go::CreateVRFKey
- Entrypoint: POST /query GraphQL mutation `CreateVRFKey`
- Attacker controls: object IDs, nested input objects, and mutation ordering
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: object identity and nested mutation inputs must stay bound to the same authorized resource
- Expected Immunefi impact: unauthorized access to sensitive node state or secrets exposed via GraphQL
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
