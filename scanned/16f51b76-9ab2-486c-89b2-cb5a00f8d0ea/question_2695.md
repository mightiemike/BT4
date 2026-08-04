# Q2695: canonicalization collision in StringUtil.encode58Check

## Question
Can an unprivileged attacker reach /jsonrpc eth_sendRawTransaction with alternate encodings or identifier forms so common/src/main/java/org/tron/common/utils/StringUtil.java::encode58Check treats two distinct users, assets, notes, orders, or permissions as the same object, mutates the wrong transaction-processing state/the resulting accounting, receipt, or index state, and causes Unauthorized transaction execution or state mutation?

## Target
- File/function: common/src/main/java/org/tron/common/utils/StringUtil.java::encode58Check
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Test base58/hex/visible variants, case and prefix differences, asset or order id encodings, and any canonicalization step that feeds authorization or settlement.
- Invariant to test: Canonicalization must map one logical object to one internal object and never collapse attacker-controlled identifiers onto a victim-controlled object.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Generate alternate but decodable identifiers through /jsonrpc eth_sendRawTransaction, track object selection, and assert they never target a different live account, asset, order, or note.
