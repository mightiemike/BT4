# Q1147: canonicalization collision in Commons.decode58Check

## Question
Can an unprivileged attacker reach /wallet/broadcasthex with alternate encodings or identifier forms so chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check treats two distinct users, assets, notes, orders, or permissions as the same object, mutates the wrong transaction-processing state/the resulting accounting, receipt, or index state, and causes Unauthorized transaction execution or state mutation?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check
- Entrypoint: /wallet/broadcasthex
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Test base58/hex/visible variants, case and prefix differences, asset or order id encodings, and any canonicalization step that feeds authorization or settlement.
- Invariant to test: Canonicalization must map one logical object to one internal object and never collapse attacker-controlled identifiers onto a victim-controlled object.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Generate alternate but decodable identifiers through /wallet/broadcasthex, track object selection, and assert they never target a different live account, asset, order, or note.
