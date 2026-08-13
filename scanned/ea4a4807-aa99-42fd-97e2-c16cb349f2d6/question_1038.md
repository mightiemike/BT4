# Q1038: Boundary preservation edge case in CreateAPIToken #1

## Question
Can an unprivileged attacker use GraphQL body, variables, aliases, fragments, and auth headers at `POST /query GraphQL mutation `CreateAPIToken`` so `CreateAPIToken` reaches a concrete path to unauthorized access to sensitive node state or secrets exposed via GraphQL by breaking the invariant that object identity and nested mutation inputs must stay bound to the same authorized resource, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/resolver/mutation.go::CreateAPIToken
- Entrypoint: POST /query GraphQL mutation `CreateAPIToken`
- Attacker controls: GraphQL body, variables, aliases, fragments, and auth headers
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: object identity and nested mutation inputs must stay bound to the same authorized resource
- Expected Immunefi impact: unauthorized access to sensitive node state or secrets exposed via GraphQL
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
