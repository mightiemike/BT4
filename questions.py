import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 23
# Repository path on GitHub
SOURCE_REPO = "protocolbuffers/protobuf-go"
# todo: the name of the repository
REPO_NAME = "protobuf-go"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    "encoding/protodelim/protodelim.go",
    "encoding/protojson/decode.go",
    "encoding/protojson/encode.go",
    "encoding/protojson/well_known_types.go",
    "encoding/prototext/decode.go",
    "encoding/prototext/encode.go",
    "encoding/protowire/wire.go",
    "internal/encoding/json/decode.go",
    "internal/encoding/json/decode_number.go",
    "internal/encoding/json/decode_string.go",
    "internal/encoding/json/decode_token.go",
    "internal/encoding/json/encode.go",
    "internal/encoding/messageset/messageset.go",
    "internal/encoding/text/decode.go",
    "internal/encoding/text/decode_number.go",
    "internal/encoding/text/decode_string.go",
    "internal/encoding/text/decode_token.go",
    "internal/encoding/text/encode.go",
    "internal/impl/api_export.go",
    "internal/impl/api_export_opaque.go",
    "internal/impl/bitmap.go",
    "internal/impl/bitmap_race.go",
    "internal/impl/checkinit.go",
    "internal/impl/codec_extension.go",
    "internal/impl/codec_field.go",
    "internal/impl/codec_field_opaque.go",
    "internal/impl/codec_map.go",
    "internal/impl/codec_message.go",
    "internal/impl/codec_message_opaque.go",
    "internal/impl/codec_messageset.go",
    "internal/impl/codec_tables.go",
    "internal/impl/codec_unsafe.go",
    "internal/impl/convert.go",
    "internal/impl/convert_list.go",
    "internal/impl/convert_map.go",
    "internal/impl/decode.go",
    "internal/impl/encode.go",
    "internal/impl/enum.go",
    "internal/impl/equal.go",
    "internal/impl/extension.go",
    "internal/impl/lazy.go",
    "internal/impl/legacy_enum.go",
    "internal/impl/legacy_export.go",
    "internal/impl/legacy_extension.go",
    "internal/impl/legacy_file.go",
    "internal/impl/legacy_message.go",
    "internal/impl/merge.go",
    "internal/impl/message.go",
    "internal/impl/message_opaque.go",
    "internal/impl/message_reflect.go",
    "internal/impl/message_reflect_field.go",
    "internal/impl/pointer_unsafe.go",
    "internal/impl/pointer_unsafe_opaque.go",
    "internal/impl/presence.go",
    "internal/impl/validate.go",
    "internal/protolazy/bufferreader.go",
    "internal/protolazy/lazy.go",
    "internal/protolazy/pointer_unsafe.go",
    "proto/checkinit.go",
    "proto/decode.go",
    "proto/encode.go",
    "proto/equal.go",
    "proto/extension.go",
    "proto/merge.go",
    "proto/messageset.go",
    "proto/proto.go",
    "proto/proto_methods.go",
    "proto/proto_reflect.go",
    "proto/reset.go",
    "proto/size.go",
    "proto/wrapperopaque.go",
    "proto/wrappers.go",
    "reflect/protoreflect/methods.go",
    "reflect/protoreflect/proto.go",
    "reflect/protoreflect/source.go",
    "reflect/protoreflect/type.go",
    "reflect/protoreflect/value.go",
    "reflect/protoreflect/value_equal.go",
    "reflect/protoreflect/value_union.go",
    "reflect/protoreflect/value_unsafe.go",
    "reflect/protoregistry/registry.go",
    "runtime/protoiface/legacy.go",
    "runtime/protoiface/methods.go",
    "runtime/protolazy/protolazy.go",
]


target_scopes = [
    "Critical/High — binary wire parsing: proto/decode.go, encoding/protowire/wire.go and internal/impl/decode.go must safely reject attacker-sent malformed tags, varints, length-delimited fields, groups and packed values. Find a reachable incorrect bounds or wire-type decision that corrupts memory through Go unsafe code or changes a security-relevant parsed field.",
    "Critical/High — generated-message fast paths: internal/impl/codec_field.go, codec_map.go, codec_message.go, codec_extension.go and codec_messageset.go decode an unauthenticated request into a trusted application schema. Find a malformed wire payload that makes the fast path disagree with the reflective path or violates field ownership, presence or map/oneof invariants with concrete confidentiality or integrity impact.",
    "Critical/High — lazy decoding and buffer ownership: internal/impl/lazy.go, internal/protolazy/*.go and runtime/protolazy/protolazy.go may defer work on bytes supplied in a network request. Find a concrete aliasing, lifetime or validation error that lets later access to the parsed message read or write the wrong bytes, bypass field validation, or cross a request boundary.",
    "Critical/High — ProtoJSON: encoding/protojson/decode.go, well_known_types.go and internal/encoding/json/decode*.go parse attacker-submitted JSON. Find a duplicate-field, number, string, oneof, null or nested-value interpretation error that makes a security-sensitive field appear validated while a different value is stored or used; show the exact application-visible mismatch.",
    "Critical/High — Any and extension resolution: proto/extension.go, encoding/protojson/well_known_types.go, reflect/protoregistry/registry.go and internal/impl/codec_extension.go resolve payload-selected types against trusted registrations. Find a request payload that is accepted as one trusted type but used as another, or bypasses a required field/extension check, producing unauthorized state or sensitive-data disclosure.",
    "Critical/High — required fields, oneofs and message sets: proto/checkinit.go, proto/messageset.go, internal/impl/checkinit.go, validate.go and codec_messageset.go handle accepted wire data. Find a payload that returns success while a field relied on for authorization is absent, conflicting or interpreted differently on a subsequent supported path.",
    "Critical/High — unsafe reflection and parsed-message reuse: internal/impl/message_reflect.go, pointer_unsafe.go, convert*.go and proto/reset.go, merge.go, proto_reflect.go move fields from parsed requests. Find a normal server flow in which attacker-controlled bytes lead to a stale pointer, aliased backing storage or cross-request data exposure; prove the value crosses a trust boundary.",
    "High — framed input and secondary text parsing: encoding/protodelim/protodelim.go, encoding/prototext/decode.go and internal/encoding/text/decode*.go consume an unprivileged client's framed or text-form request only where an application actually exposes that parser. Find an exact framing or tokenization error that creates a demonstrated authorization/integrity failure or sensitive-data disclosure; account for this surface's weaker policy priority.",
    "Critical/High — parse-to-use consistency: proto/decode.go, encoding/protojson/decode.go, internal/impl/decode.go and the production encode/reflect paths must preserve security-relevant field semantics after successful parse. Find a concrete request whose accepted value, presence, unknown fields, or type identity changes before an application makes a documented security decision; do not assume canonical serialization or a second service reparsing original bytes.",
    "Critical/High blind spot — inspect every scoped production parser helper and transition for an unstated assumption about trusted schema versus attacker-owned bytes: especially lazy validation, legacy MessageSet, unknown fields, proto2 required fields, and Any. Require a direct unprivileged request path and a demonstrable confidentiality, integrity or unsafe-memory consequence that the preceding scopes miss.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """Generate focused security questions for one protobuf-go target."""
    prompt = f"""Generate 40–80 distinct, high-signal security audit questions for:
{target_file}

Treat `File Name:` as the exact production file and `Scope:` as the only target impact. Assume full repo access. Attacker is an unprivileged client sending crafted binary protobuf or ProtoJSON to an exposed Go service using a trusted schema and ordinary API defaults; use ProtoText or framed input only if the entry point actually exposes it. No privileged access, malicious peer/node, untrusted schema, custom resolver, local code execution, or victim cooperation. Exclude tests, mocks, generated files, docs and build-only issues.

Prioritize direct parsing of untrusted bytes: wire bounds/types, nested depth handling without generic resource-growth claims, generated versus reflective decoding, lazy validation/buffer ownership, oneof/map/extension/Any semantics, required-field checks, and ProtoJSON tokens. Follow bytes through exact functions into the application-visible field or unsafe memory operation. Do not treat noncanonical wire encodings, duplicate JSON keys, trusted in-memory object misuse, or cross-service reparsing alone as vulnerabilities. Do not ask about unbounded memory/CPU consumption, generic panics, speculative gateway policy, or mere format disagreement.

Every question must name a real entry point and concrete payload shape, trusted-schema preconditions, exact target symbol, execution sequence, violated invariant, scoped Critical/High impact (or a demonstrable Medium issue if within the policy), and a minimal Go test/proof. Avoid repeated root causes. Output only valid Python, no markdown:
questions = [
    "[File: {target_file}] [Function: symbol] Can an unauthenticated client send PAYLOAD via ENTRY_POINT with TRUSTED_SCHEMA and trigger SEQUENCE, violating INVARIANT and causing IMPACT? Proof: Go test INPUT and expected FIELD/ERROR/SAFETY assertion.",
]
"""
    return prompt


def audit_format(security_question: str) -> str:
    """Generate a focused protobuf-go claim review prompt."""
    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Trace only this question and its scoped impact in production protobuf-go code.
- Start with an unprivileged client's concrete binary or ProtoJSON request to a service using trusted schemas and default parsing; allow ProtoText/framing only when exposed. No malicious peer/node, privileged role, custom resolver, untrusted schema or local code execution.
- Apply repository SECURITY.md and the Protobuf threat model: binary and ProtoJSON parsing are hardened; text parsing is secondary; schemas and in-memory objects are trusted. Ignore generated/test/mock/build-only paths, noncanonical-encoding claims alone, mere parse disagreements, generic panic and unbounded resource claims.
- Prove exact file/function, payload, checks, resulting security-relevant field or unsafe-memory effect, and realistic confidentiality/integrity impact. Classify Critical, High or supported Medium from evidence, never by analogy alone.

## Output
If valid, output exactly:
### Title
[Bug statement] - ([File: file_path])
### Summary
[2–3 sentences]
### Finding Description
[Root cause, request path, checks and why they fail]
### Impact Explanation
[Concrete impact and justified severity]
### Likelihood Explanation
[Required attacker capabilities and repeatability]
### Recommendation
[Specific fix]
### Proof of Concept
[Minimal Go test or exact request bytes and expected result]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """Scan a report for a reachable protobuf-go analog."""
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use the report as a bug-class hint, not evidence. Search scoped production protobuf-go code for the same broken invariant on a reachable unprivileged request path. Assume a trusted schema and default binary or ProtoJSON parser; use ProtoText or protodelim only if an endpoint exposes it. No malicious peer/node, privileged caller, custom resolver or attacker-supplied descriptor.
- Map the report's source, parser state, validation step and sink to exact protobuf-go functions. Check both generic and generated-message paths, lazy versus eager decode, unknown/extension/oneof/map handling, Any and well-known JSON types, required-field checks and buffer ownership. Follow accepted values into a concrete confidentiality/integrity decision or unsafe-memory effect.
- Apply SECURITY.md and the Protobuf threat model. Reject analogs based only on noncanonical serialization, duplicate JSON keys, cross-service reparsing original bytes, trusted in-memory misuse, generic panic, unbounded resources, tests/mocks/generated code or another project's behavior. Do not assume a generic Go service has a specific authorization rule.
- Accept Critical/High, or Medium when the evidence and live scope support it. Require exact file/function, request bytes or JSON, trusted-schema preconditions, failing check and a minimal Go proof. If no such path exists, reject.

## Output (Strict)
If valid, output exactly:
### Title
[Clear vulnerability statement] - ([File: file_path])
### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """Validate a protobuf-go claim against its actual security boundary."""
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the claim; do not invent another finding or upgrade severity without proof. Apply this repo's SECURITY.md and Researcher.Md when present, plus the Protobuf security policy and current Google OSS VRP rules; do not claim this repo has a bounty tier unless verified.
- Binary wire and ProtoJSON parsing of untrusted bytes with trusted schemas are the primary hardened surfaces. ProtoText is secondary. Offline compiler, untrusted schemas/descriptors, direct misuse of trusted in-memory messages, noncanonical serialization, and gateway reparsing original bytes have different or excluded threat boundaries.
- Attacker must be an unprivileged client reaching a real exposed parsing API with a concrete request. No malicious peer/node, privileged access, custom resolver, local code execution, test-only path, or generated-file root cause.
- Require exact file/function/line, payload, trusted-schema preconditions, path from input through failed checks to demonstrated unauthorized disclosure/state change or unsafe-memory effect, and a reproducible Go proof. A panic, duplicate JSON key, format difference, or resource-growth claim alone is insufficient.
- Classify by proven impact: Critical for demonstrated server compromise or comparable cross-boundary control; High for substantial unauthorized disclosure/integrity change or exploitable unsafe-memory corruption; Medium for a narrower but concrete security impact that the live program accepts. Reject Low, informational, speculative and best-practice claims. Respect program-specific exclusions if they differ.

## Output
If valid, output exactly:
Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])
## Summary
[2–3 sentences]
## Finding Description
[Exact path, root cause and failed checks]
## Impact Explanation
[Proven impact, severity and policy basis]
## Likelihood Explanation
[Preconditions and repeatability]
## Recommendation
[Specific fix]
## Proof of Concept
[Request bytes/JSON, trusted schema and Go reproduction]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
