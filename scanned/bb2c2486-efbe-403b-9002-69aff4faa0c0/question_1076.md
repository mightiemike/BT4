# Q1076: GraphQL auth bypass in CreateJob

## Question
Can an unprivileged attacker send GraphQL body, variables, aliases, fragments, and auth headers through `POST /query GraphQL mutation `CreateJob`` so `CreateJob` performs a privileged mutation without the REST-equivalent authorization result, leading to authentication bypass into a privileged mutation and violating GraphQL authorization must be at least as strict as the equivalent REST mutation path?

## Target
- File/function: core/web/resolver/mutation.go::CreateJob
- Entrypoint: POST /query GraphQL mutation `CreateJob`
- Attacker controls: GraphQL body, variables, aliases, fragments, and auth headers
- Exploit idea: Use aliased/nested GraphQL mutations with shared variables to prove whether GraphQL authz ever diverges from the equivalent REST mutation.
- Invariant to test: GraphQL authorization must be at least as strict as the equivalent REST mutation path
- Expected Immunefi impact: authentication bypass into a privileged mutation
- Fast validation: Issue the minimal mutation plus aliased/fragmented variants; assert identical authz and object binding to the REST-equivalent operation.
