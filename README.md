# TSN Lab — scenario designer

A local **Vue 3 + TypeScript + Vue Flow** editor for OMNeT++ 6.4 / INET 4.7 networks. Simulations run exclusively in **Cmdenv**; logs and measurements appear in the browser.

## Start

From `black-box-cbs/`, install Python dependencies in a virtual environment and build the frontend:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web
npm install
npm run build
cd ..
TSN_PYTHON=.venv/bin/python scripts/serve.sh
```

Open **http://127.0.0.1:8000**. The backend binds only to loopback. It uses Flask and Pydantic; no external database or cloud service is required. For frontend development, run `npm run dev` in `web/` alongside the backend and open http://127.0.0.1:5173; Vite proxies `/api` to port 8000.

The simulation subprocess must have a working `opp_env` environment with the installed INET release library. The worker inherits a genuine active `opp_env` shell, or invokes `opp_env run inet-4.7.0 --no-build -c ...` from the workspace. Start the backend inside an existing working `opp_env shell inet-4.7.0` when possible. This installation rejects plain `source setenv` launches. `OMNETPP_ROOT` and `INET_ROOT` can select the installed distributions within that environment. Do not fabricate `OPP_ENV_VERSION` to bypass the installation guard.

## Use

1. Load a reviewed 1-, 2-, 4-, 8-switch or branched example. Add/drag/duplicate devices and switches. Add switch ports, then connect explicit node/port pairs in Ethernet links.
2. Select each node to set permitted VLANs, PCP-to-class mappings, per-class FIFO capacity, strict priority or CBS idleSlope, and optimizer eligibility. Priority increases with the class index.
3. Edit periodic UDP flows in Traffic: endpoints, payload bytes, interval, start/stop, VLAN, PCP and deadline. Edit simulation duration, warm-up and seed below the workspace.
4. Validate, then inspect Generated files. Export the complete ZIP or scenario JSON. Save immutable revisions and reopen them; the current browser draft is also saved locally.
5. Run simulation. Inspect process logs and the reproducible command in Runs, cancel jobs, and download all run artifacts.
6. Select a completed run to view per-flow metrics, latency traces/CDFs and queue/CBS traces. Export CSV or inspect another completed run for comparison.

Keep traffic stop plus its deadline below simulation end. Measurement cohorts use actual source generation times after warm-up. Latency includes the source and network queues. Jitter is signed delay difference between consecutive generated packets when both arrive; mean absolute IPDV is reported separately. Missing packets at the observation horizon are not labeled confirmed network drops. No-delivery latency is unavailable rather than zero. Goodput uses unique delivered cohort payload over `[warmup, simulation_end]`. Full trace data and raw scavetool CSV-R remain in artifacts.

## CLI and APIs

The CLI and HTTP service share the same typed compiler and evaluator:

```bash
PYTHONPATH=backend python3 -m designer.cli validate scenarios/line-1.json
PYTHONPATH=backend python3 -m designer.cli generate scenarios/line-1.json --output var/export
PYTHONPATH=backend python3 -m designer.cli run scenarios/line-1.json
PYTHONPATH=backend python3 -m designer.cli extract scenarios/line-1.json --csv var/runs/JOB_ID/raw.csv
PYTHONPATH=backend python3 -m pytest -q
```

Use your virtual environment Python in these commands when dependencies are installed there.

- `GET /api/templates`, `GET /api/capabilities`: reviewed examples and supported features.
- `POST /api/compile`, `POST /api/export`: accept the scenario document; return generated files/diagnostics or a ZIP.
- `GET/POST /api/scenarios`: list/save immutable scenario revisions.
- `GET/POST /api/jobs`: list runs or submit `{ "scenario": ..., "candidates": {"sw0/2/1": 15000000} }`. Candidates may change only selected CBS port/classes. Response contains a job ID.
- `GET /api/jobs/ID`: status, logs, command, frozen scenario and final metrics.
- `GET /api/jobs/ID/events`: SSE state updates; the UI currently polls job details to retrieve logs.
- `POST /api/jobs/ID/cancel`, `GET /api/jobs/ID/artifacts`: process cancellation and artifact ZIP.

SQLite revisions/jobs, generated bundles, native outputs and logs live in ignored `var/`. Set `TSN_DATA_DIR` to use a different local data directory. Run one backend process; it serializes simulations and marks unfinished jobs interrupted on server restart. The CLI waits for completion and returns a failing exit code for unsuccessful runs. Failed/cancelled jobs never produce final metrics.

## Current profile and limits

Implemented: connected acyclic tagged Ethernet networks, single-interface end devices, explicit switch ports, periodic fixed-size IPv4/UDP flows, VLAN admission and VLAN-aware static forwarding, PCP mappings, finite drop-tail class queues, strict priority/CBS, typed candidate overrides, immutable job inputs, serial jobs/cancellation, official scavetool extraction, latency/IPDV/deadline/delivery metrics and raw queue diagnostics. Node dragging has undo/redo; field edits are persisted as drafts/revisions. All configured interfaces use the priority queue container; choose strict priority classes on hosts when source CBS is unwanted. The initial credit policy retains INET defaults, including unbounded credit; it is recorded in generated INI comments and must be considered when defining research scenarios.

Unavailable features are labeled in the UI: buffered ingress priority queues, Qci, TAS, ATS, gPTP, preemption, FRER and cut-through. Access/native VLAN translation, rings/meshes, stochastic/burst traffic, automatic repetition campaigns, evaluation caching, source-linked editor navigation and the optimization algorithms remain future work. A simulation is limited to two hours of wall time and two million expected packets per flow. Large traces require deliberate disk/memory budgeting.

## Verification in the implementation environment

Python behavior/API tests and actual `opp_nedtool` syntax validation of generated NED are available. Full browser/build and real simulation verification are **not complete** in the current sandbox:

- npm/pip downloads fail DNS resolution; Vue/Vite dependencies cannot be installed here, so frontend compilation and browser interactions still need verification after installation.
- `opp_env run` fails while evaluating the sandbox workspace Git/flake context. A direct Nix environment attempt also cannot connect to its daemon. The run failure is preserved in the job log; no successful TSN simulation is claimed.

On a working host, complete acceptance by running the 1-switch example, checking its emitted VLAN/PCP and actual queue assignment, then the multi-hop examples. Verify that source/sink vectors appear in the official CSV-R export and inspect CBS credit/throughput behavior before using results in the thesis. These runtime checks are necessary beyond syntax validation.

The implementation follows [the research implementation plan](docs/scenario-design-implementation-plan.md), updated to Vue and Cmdenv. The later feature profiles in that document are a roadmap, not claims of current support.
