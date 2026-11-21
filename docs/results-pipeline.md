# How simulation results are produced

The browser configures the model and displays measurements. It does not generate packet measurements or parse native OMNeT++ output. The Python service manages three explicit stages, all within the same run directory.

## 1. Run the simulation

The compiler freezes the submitted scenario, resolved port/class/flow mappings, NED, INI, and manifest in `var/runs/<job-id>/`. Compiler 3.3 uses native EthernetSourceApp/EthernetSinkApp applications and FlowMeasurementStarter/FlowMeasurementRecorder. `manifest.json` maps each source and every receiver recorder, and records `measurement_version = 3` plus `generation_horizon = "simulation_end"`. Periodic/burst sources run until the simulation ends; no separate traffic stop or drain period is configured. Older bundles retain their original configuration and extraction rules.

The backend launches `opp_run -u Cmdenv` with the generated network, INET library and run-specific results directory. This may enter the installed `opp_env` environment. Cmdenv writes:

- `results/*.vec`: sampled signals with simulation timestamps, such as packet generation observations, packet lifetime, queue occupancy and credits.
- `results/*.sca`: aggregate scalar values, including source/sink packet counts.
- `results/*.vci`: vector indexes used by OMNeT++ tools.

These are the authoritative simulation outputs. `run.log` contains the simulator output; `command.txt` contains the exact launch command. A successful exit and the configured simulation-time-limit completion message are required before result processing proceeds.

## 2. Export the observations

The backend runs the installed standalone `opp_scavetool` against those `.vec` and `.sca` files:

```text
opp_scavetool export -F JSON -T sv -f <selected-signals> \
    -x pythonFlavoured=false -x indentSize=0 -x precision=15 \
    -o native-results.json.tmp <run.vec> <run.sca>
```

This step does not launch Cmdenv or enter another `opp_env` shell. The exact command is in `export-command.txt`, and its output is in `export.log`. A successful temporary export is renamed to `native-results.json`.

The filter selects:

- Native source `packetLengths:vector` and `packets:count`.
- Every receiver recorder's per-flow mean elapsed-time, timing breakdown, jitter, flow data rate and optional bit elapsed-time region vectors, plus sink counts.
- Selected queue occupancy, queueing, CBS credits, overflow counters and channel busy vectors.

INET demux labels precede statistic names: for example, `control:meanBitElapsedTimePerPacket:vector`. The reader selects the exact flow label and recorder, never combines a multicast receiver's samples with another receiver. Region vectors may contain several observations per packet; they remain diagnostic traces and are not counted as packets.

For v1/v2 archives the exporter instead selects the original sink packet lifetime and, when specified in that frozen manifest, sequence-number vectors.

JSON contains numeric arrays (`time` and `value`), not a space-separated vector stuffed into one CSV field. The parser requires exactly one run, so measurements from separate seeds cannot be mixed silently. Native result files remain available for additional analysis beyond the signals selected for the GUI.

## 3. Calculate metrics

`results.py` reads the official export. `metrics.py` calculates the same metrics for a normal run and for recovery. It writes `metrics.json` atomically; the GUI reads that file through the job API. In **Analysis**, select a completed run using its simulation name, creation time and run ID. Its statistics, plots, CSV and artifact links use that selected run, independently of the editor and run-log inspection. Changing the selected run clears the old statistics and comparisons; late responses from earlier selections are ignored.

For measurement v3, per flow and receiver:

1. Source packet-length timestamps supply actual generations in `warmup <= generation_time < simulation_end`. The end is exclusive for cohort accounting, including when a source event shares the limit timestamp.
2. `flow:meanBitElapsedTimePerPacket:vector` supplies one receiver sample per measured packet. The native elapsed-time tag starts at the source measurement starter, so `arrival - delay` identifies the generation timestamp.
3. Match receptions to a multiset of generation timestamps within `min(period / 100, 1 ns)`. A burst has multiple available observations at the same timestamp; each reception consumes one. Unattributable/excess samples fail extraction. A lazily absent demux vector counts as no receptions only when the native sink count proves the sink was instrumented.
4. Latency summaries use these native mean bit elapsed times. They cover source/network queueing through the application recorder. Jitter is signed delay difference between successive received cohort packets, consistent with INET's `packetJitter` filter; it may span a missing packet. The first sample has no jitter. This differs from legacy sequence-adjacent IPDV.
5. Timing dimensions are summarized separately from the native mean-per-packet vectors: queueing, processing, complete-packet transmission, propagation and delaying. Their definitions differ, so their sum is not assumed to equal elapsed time. Available raw native vectors remain in the official JSON/artifacts.
6. Deadline misses include late and missing cohort packets whose deadlines mature by simulation end. `deadline_unmatured` counts packets with later deadlines; they are excluded from `deadline_denominator` and never classified as misses. If no deadline has matured, the miss ratio is unavailable rather than zero. Goodput is received cohort payload bits divided by `simulation_end - warmup`. Each multicast receiver has its own denominator, missing count and goodput; a source is not counted as generating a new packet for every destination.
7. Native timing observations do not carry the former custom sequence IDs. Duplicate and out-of-order counts are **unavailable**, not zero. Timestamp-multiplicity matching assumes the validated static routes deliver at most one copy per configured receiver. It cannot distinguish a duplicate that replaces a lost equal-timestamp burst packet. Do not use this measurement version to claim duplicate-elimination performance.

`metrics.json` records the measurement version, definitions and identity limit. The GUI reports multicast rows as `flow@receiver` and exposes timing-component summaries. Empty latency remains unavailable.

For frozen pre-3.2 measurement-v3 archives without `generation_horizon`, the cohort still ends at the archived flow stop. For historical measurement v1/v2, the original definitions remain:

1. Source vector timestamps identify packets actually generated. Only packets generated during `warmup <= generation_time < traffic_stop` belong to the measurement cohort. This uses actual observations, not a guessed count from the configured interval.
2. A sink vector sample supplies reception time `r` and packet lifetime `d`. The inferred generation time is `r - d`.
3. Measurement-v2 archives match receiver/source sequence observations and verify the inferred generation time within `min(interval / 100, 1 ns)`. Different packets in a burst may share a timestamp; their sequence IDs distinguish them. Duplicate receptions of an ID count once. Legacy v1 manifests without sequence observations retain nearest-timestamp matching for their original periodic UDP runs. Missing required observations or unmatched samples fail extraction.
4. End-to-end delay is `r - generation_time`, as supplied by the packet lifetime observation. It includes source/network queueing. The GUI reports the sample count, mean, standard deviation and linearly interpolated p50/p95/p99 values.
5. Signed IPDV is the difference between delays of consecutive generated packets when both have receptions. Gaps are skipped and counted. Mean absolute IPDV is reported separately.
6. A packet misses its deadline if no reception occurs by `generation_time + deadline`, including missing packets with matured deadlines. A small timestamp tolerance is used when comparing floating-point observations. The denominator includes every cohort packet whose deadline matured, not only received packets.
7. `missing_at_horizon = generated - unique_received`. This is not a claim of a confirmed physical network drop. Confirmed per-flow drops remain unavailable; raw queue-overflow counters are separate diagnostics.
8. Goodput is unique received cohort payload bits divided by `simulation_end - warmup`.

A flow with no receptions has unavailable latency, not zero latency. Missing observations and malformed exports are errors, not zero-valued performance measurements. Queue diagnostic traces are exported observations; they do not substitute for per-flow delay accounting.

`pipeline.json` records the status of simulation, export and calculation. Failures identify their stage; `processing.log` records metric-processing errors. A job becomes `completed` only after `metrics.json` has been published.

## Recovery and older CSV files

**Recover results without rerunning** invokes the same export-and-calculate function as a normal job. It verifies that the saved simulation completed, uses that job's frozen scenario/manifest, and re-exports its native files to JSON. It never starts `opp_run` or regenerates traffic. Only derived JSON/metrics and processing logs are updated.

If native files are unavailable, an existing JSON export can be reused. Only when the archive contains an old `raw.csv` and no native/JSON files does the compatibility reader use CSV-R. That reader raises the CSV field limit immediately before reading, including when another library reset it after imports. New runs do not depend on a CSV field limit.

Without a web server, recovery is also available from the application root:

```bash
PYTHONPATH=backend python3 -m designer.recover JOB_ID
```

Use the same Python/virtual environment as the backend. This command uses the saved job configuration; a separate scenario filename is not required. The CLI `extract` command also accepts `--manifest path/to/manifest.json` and automatically uses a manifest beside the result export (or its parent directory) when available. This prevents reinterpreting old output with the current compiler.

## Loading the updated backend

Changing source files or running `npm run build` does not replace an already running Python server. Stop the existing backend with Ctrl+C and restart it using the usual launch command, for example:

```bash
TSN_PYTHON=.venv/bin/python scripts/serve.sh
```

Run Simulation must show **Ethernet compiler 3.3**, **Results engine: json-v1** and a new backend start time. `GET /api/health` exposes the loaded engine version, start time and PID. If the frontend detects a missing/older engine, it explains the restart requirement and disables launching new jobs through that old process.

## Per-link packet observations

Compiler 3.3 optionally records per-port native packet-event JSON and retains transmitter state/duration/propagation signals. The shared processing path builds per-link interval artifacts after latency extraction; old runs without this telemetry remain usable for latency. See [analysis.md](analysis.md) for timing boundaries, capture coverage, interval reconstruction and the bounded-window API.
