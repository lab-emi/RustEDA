# Antenna metadata span scanning — private checkpoint

The single-process antenna adapter previously dispatched every byte of the large native reports through Python. It now searches ordinary byte spans with compiled regular expressions, retaining the existing bracket/string state machine and bounded selected-field JSON decoding. Raw native reports and the independent verifier are unchanged. No unselected geometry arrays are decoded.

## Bounds and compatibility

The scan keeps one 64 KiB input chunk, at most64 nested containers,128 top-level keys and an8 KiB key. The existing report, selected-field and aggregate selected-byte limits remain authoritative. Expected parser RSS is below128 MiB; the isolated suite uses one worker,1 GiB RAM/512 MiB swap and180 s wall guard. No graph search or path materialization was introduced.

This remains a metadata reader: skipped values retain the prior bracket/string grammar rather than gaining a second JSON semantic oracle. Selected values still use the standard decoder. Diagnostic precedence can differ for multiply-invalid, over-budget inputs; acceptance and selected values are preserved. An escaped byte is consumed explicitly across refills, and closing a container does not prematurely end its top-level value.

## Measured evidence

The retained successful private flow contains439 real logical operations. Every selected field in its435 parsed check/repair reports matched the previous scanner exactly (679,166,798 input bytes). Sequential report identity checks, metadata scan, selected decoding and fingerprinting on CPUs8,10 took73.290 s before and14.267 s after. Peak RSS for the complete comparison was22,548 KiB. Short protocol tests overlapped part of the before pass; these numbers are metadata-pass measurements, not a new whole-flow speedup.

Evidence directory: `.work/perf-20261030/metadata/`.

- `result.json`: fa64d9f0b0b6e143155364e17a68e8c385a4824cd416998397f98ca61c74d3cc (1,393 bytes)
- exact executed `compare_fields.py`: 14359e723173a9bcf3bc8dc5bb4ab8d052511607e294489eadb01ebbd376a54f (2,850 bytes)
- `compare.time`: 61cc828fa82caca2ec88f68b702ddc33a9c0d96093c30f775bf7f8d2250f7f60 (801 bytes)
- Nine new parser test methods pass, including250 standard-decoder cases,64 KiB escape/UTF-8 boundaries, skipped malformed literals, controls, nesting and all byte/key limits. Test peak RSS27,132 KiB.
- All32 IHP worker/session/parallel protocol tests pass with the qualified GNU timer. The first isolated run skipped9 worker checks because its local timer fixture was absent; that run was retained and the fixture was supplied before the no-skip rerun.
- Independent read-only review found no state/refill/cap correctness blocker.

The accepted full CPU run remains the prior frozen source/ELF:1,099.765 s,16/16 gates, six native hashes identical to run16. This parser change still needs a fresh complete acceptance run when combined with the next CPU engine candidate. No EDA correctness or new full-flow claim comes from these metadata tests. No publication or push.
