# LogScope v1 specification

A dependency-free C17 command-line investigation tool for synthetic or locally
supplied HTTP access logs. It reads one pass, never stores input rows, and emits
human text or a versioned JSON report. No network access at runtime.

Input is LogScope TSV v1: six ASCII fields, no header, separated by one tab:
`epoch_ms METHOD request_target status response_bytes latency_us`.
Unsigned integers have decimal digits only. Timestamp/bytes span uint64; latency
is at most 86,400,000,000 microseconds (24 h), status 100..599, method 1..16
uppercase ASCII letters, target 1..2048 visible ASCII characters, starts with /
or is *. Line limit 8192 bytes excluding LF; CRLF and final unterminated line
accepted. Embedded NUL, extra/missing columns, signed values, integer overflow,
control/non-ASCII bytes and overlong fields are rejected. Query stripped for
endpoint grouping by default; a flag keeps it. No percent decoding or route
inference. Timestamp is UTC epoch milliseconds; empty input is valid.

Lifetime summaries: exact counts/status codes/5xx/bytes/min/max/sum latency;
quantiles represented by containing power-of-two intervals (not point estimates).
All uint64 accumulation is checked; arithmetic overflow aborts without a report.

Top endpoints: two independent fixed-capacity Space-Saving summaries for traffic
and 5xx. Each row exposes lower=count-error and upper=count. Key includes method.
All keys exact when distinct count <= capacity; otherwise ranking approximate.
Dictionary and min-heap occupy O(capacity), updates O(key length + log capacity)
expected with chaining; adversarial hashing can degrade to O(capacity).

Minute window: fixed ring retaining the latest W event-time minute numbers.
Out-of-order records inside the window update buckets; older records still count
in lifetime/top summaries but are counted as outside_window. Evictions counted.
Reported buckets are sorted; empty minutes omitted. No assumption of sorted logs.
The default W=120, configurable 1..10080. Top capacity default 128, range 1..4096.
Input-line and read buffers fixed; memory independent of file length/cardinality.

CLI: analyze INPUT (or - stdin), --baseline FILE, --json, --strict,
--from MS inclusive, --until MS exclusive, --method METHOD, --status MIN:MAX,
--path-prefix PREFIX, --min-latency-us N, --keep-query, --capacity N,
--window-minutes N, --top N, --alert-min-requests N, --alert-rate PERCENT.
Filters apply identically to incident and baseline; rejected lines counted before
filtering. Strict mode aborts on first rejected line (line number, no log content).
Default tolerant mode counts malformed lines by reason, retaining a limited sample
of line numbers only. File and stdin read errors fatal; output errors fatal.

Baseline comparison: total request ratio (not throughput), error-rate change in
percentage points, mean latency ratio. Undefined denominators produce null.
Minute alerts: >= minimum requests AND 5xx rate >= threshold AND (if nonempty
baseline supplied) >= 3 * baseline rate and >= baseline rate + 5 percentage points.
These are explainable heuristics, not significance tests or causality claims.

Exit codes: 0 report, 1 CLI usage, 2 IO, 3 invalid strict input or counter overflow.
JSON version 1; integer values emitted as integers (consumers must preserve uint64).
No real/private logs; included fixtures and benchmarks entirely synthetic.
