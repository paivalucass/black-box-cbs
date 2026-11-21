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

Charts use pinned ECharts 6.0.0. For offline operation, run `npm run charts:install` in `web/` before building; otherwise the browser loads the pinned CDN distribution.

The simulation subprocess must have a working `opp_env` environment with the installed INET release library. The worker inherits a genuine active `opp_env` shell, or invokes `opp_env run inet-4.7.0 --no-build -c ...` from the workspace. Start the backend inside an existing working `opp_env shell inet-4.7.0` when possible. This installation rejects plain `source setenv` launches. `OMNETPP_ROOT` and `INET_ROOT` can select the installed distributions within that environment. Do not fabricate `OPP_ENV_VERSION` to bypass the installation guard.

## Use

1. Enter a **Simulation name**. Start with **New simulation**, or choose **Load template** to populate the network while retaining the name you entered.
2. Use the four sidebar sections: **Experiment** for saved models and experiment settings; **Build Simulation** for **Topology**, **Traffic** and **Review**; **Run Simulation** for launching jobs and inspecting logs; and **Analysis** for a selected completed run’s **Latency** and **Per link flow occupancy** views. Topology includes collapsed node/queue configuration and links chosen by their two nodes. Ports are automatic and propagation delay comes from cable length. Traffic flows start collapsed; receivers use checkboxes in a dropdown, and routes use a shortest-first dropdown. Review contains generated files, resolved paths and feature support. Values persist when switching sections. Square controls and flat panels keep the interface simple; the sidebar’s **Dark / White** appearance setting is remembered across reloads. Traffic includes periodic/burst/once modes, start offset, multicast receivers and route overrides; Nodes & queues sets shared node clock drift. Nodes & queues has independent queue/class counts and both mapping tables; see [simulation configuration](docs/simulation-configuration.md).
3. Click **Save simulation** to create or update a named model. **Open simulation** loads a saved model. Change the name and choose **Save as copy** to create an independent model. Templates remain unchanged. Drafts can be saved before they are valid to run.
4. Validate, inspect generated files, and export the bundle or configuration JSON. Existing revision records are migrated to named models without deleting the originals; duplicate legacy names receive numbered suffixes.
5. In **Run Simulation**, choose a saved simulation or **Current editor** from the dropdown, then run it. Saved selections use the latest saved model; Current editor includes unsaved changes. Running a saved model does not replace the editor draft. Inspect logs, cancel jobs and download artifacts here. Each run retains its own immutable configuration.
6. In **Analysis**, select a completed run by simulation name, timestamp and run ID to view per-flow statistics, plots, timing components and CSV exports. Latency shows min/max/mean in milliseconds and an ECharts percentage histogram. Per link flow occupancy shows packet queueing, transmission and propagation intervals, by link direction and time window. The displayed configuration and artifacts belong to that selected run. See [analysis and recording](docs/analysis.md). **Analyse / Open in Analysis** shortcuts also appear in the run history. Failed extraction can still be recovered in Run Simulation without rerunning.

Periodic and burst traffic continues from its start offset until simulation end; there is no per-flow Stop parameter. Compiler **3.3** follows the supplied sample using native INET modules. The template menu contains five larger research networks: automotive zonal mesh, industrial production, backbone ring, leaf–spine fabric and cascaded CBS bottlenecks. They have 48–96 end devices, 12–16 switches and 198–396 flows, with bursts, multicast, clock drift and mixed link speeds. See [the template catalogue](docs/research-templates.md) for workloads, reservations and regeneration. The Experiment tab also lets you name the generated NED network.

New measurement-v3 runs report native flow elapsed time and timing breakdowns separately per receiver. Jitter is the signed difference of successive received cohort delays. Warm-up uses actual generation observations; equal-timestamp burst packets retain their multiplicity. Packets still queued or in transit at simulation end remain missing at the horizon. Deadlines not yet reached are reported separately and excluded from the deadline-miss ratio. Missing-at-horizon counts are not confirmed network drops, and duplicate/reordering counts are unavailable in native timing measurements. Historical v1/v2 archives retain their original definitions. See [the results pipeline](docs/results-pipeline.md) for attribution, formulas and limitations.

## CLI and APIs

The CLI and HTTP service share the same typed compiler and evaluator:

```bash
PYTHONPATH=backend python3 -m designer.cli validate scenarios/01-automotive-zonal.json
PYTHONPATH=backend python3 -m designer.cli generate scenarios/01-automotive-zonal.json --output var/export
PYTHONPATH=backend python3 -m designer.cli run scenarios/01-automotive-zonal.json
PYTHONPATH=backend python3 -m designer.cli extract scenarios/01-automotive-zonal.json --results var/runs/JOB_ID/native-results.json
PYTHONPATH=backend python3 -m designer.recover JOB_ID
PYTHONPATH=backend python3 -m pytest -q
npm --prefix web test
```

Use your virtual environment Python in these commands when dependencies are installed there.

- `GET /api/health`: loaded results-engine version (`json-v1`), backend start time and PID. Restart the Python server after backend changes; rebuilding Vue does not reload it.
- `GET /api/templates`, `GET /api/capabilities`: reviewed examples and supported features.
- `POST /api/routes`: paged, shortest-first route discovery for a scenario, flow ID and receiver; no simulation is launched.
- `POST /api/compile`, `POST /api/export`: accept the scenario document; return generated files/diagnostics or a ZIP.
- `GET/POST /api/scenarios`: list/create named simulation models; responses contain `id`, `name`, timestamps and `scenario`. Names must be unique (case insensitive); conflicts return HTTP 409.
- `GET/PUT /api/scenarios/ID`: open/update a model. Saving accepts schema-valid drafts; compile/run still require valid topology and traffic.
- `POST /api/scenarios/validate`: validate/normalize an imported draft without saving or running it.
- `GET/POST /api/jobs`: list runs or submit `{ "scenario": ..., "candidates": {"sw0/2/1": 15000000} }`. Candidates may change only selected CBS port/classes. Response contains a job ID.
- `GET /api/jobs/ID`: status, logs, command, frozen scenario and final metrics.
- `GET /api/jobs/ID/events`: SSE state updates; the UI currently polls job details to retrieve logs.
- `GET /api/jobs/ID/occupancy`: bounded per-link packet intervals for a selected simulation-time window. Requires a run recorded by compiler 3.3 with link recording enabled.
- `POST /api/jobs/ID/extract`: re-export existing native files to JSON and recalculate metrics for a finished failed/interrupted job. This uses the same processing path as a normal run and never restarts Cmdenv.
- `POST /api/jobs/ID/cancel`, `GET /api/jobs/ID/artifacts`: process cancellation and artifact ZIP.

SQLite named models/jobs, generated bundles, native outputs and logs live in ignored `var/`. Set `TSN_DATA_DIR` to use a different local data directory. Run one backend process; it serializes simulations and marks unfinished jobs interrupted on server restart. The CLI waits for completion and returns a failing exit code for unsuccessful runs. Failed/cancelled jobs never produce final metrics.

## Current profile and limits

Implemented: connected static-routed tagged Ethernet networks including physical cycles, single-port devices, automatically allocated switch ports, unicast/multicast, periodic/burst/once traffic, shared node clocks, VLAN admission, PCP→class→queue mappings, fixed higher-index-first queue priority, finite queues and standard strict priority/CBS. New bundles extend `TsnNetworkBase`; strict-priority ports use `PriorityQueue`, and mixed/CBS ports use `Ieee8021qTimeAwareShaper` with native transmission-selection algorithms. No custom source/sink/shaper helper NED or new C++ compilation is required. Added switch-processing/PHY delays remain excluded.

The service retains named saves, optimizer candidate keys, immutable run inputs, serial jobs/cancellation and official JSON extraction. CBS credit policies retain INET defaults. Unsupported: ingress priority buffers, Qci, TAS, ATS, gPTP, preemption, FRER, cut-through, arbitrary broadcast/control traffic on cyclic graphs, native/access VLAN translation, stochastic traffic, repetition campaigns, caching and optimization algorithms. Runs are limited to two hours and two million expected packets per flow.

## Verification in the implementation environment

The frontend passes `vue-tsc` and the production Vite build. Python tests cover named saves/updates/copies, legacy migration, simulation validation, official scavetool export, large CSV vector fields, missing/late packets, cancellation and recovery.

The reported `field larger than field limit (131072)` was a post-processing failure, not a simulation failure. New runs now use the official scavetool **JSON** exporter, eliminating packed CSV from the normal path. Only old archives without native files use the compatibility CSV reader, which sets its limit at read time. Simulation, export and calculation have separate recorded stages/logs; recovery shares the same export/calculation function as new jobs.

During the earlier results-pipeline update, three affected legacy runs were recovered by re-exporting their saved `.vec`/`.sca` files and computing metrics through the API. The first-template run has 1,000 control packets and 5,000 best-effort packets received, with mean application latency 171.28 µs and 188.4 µs respectively. Its selected JSON export contains 31 vectors and 52 scalars (~1.7 MB), compared with the previous all-signals CSV-R export (~22 MB).

Restart the Python backend after updating code. Run Simulation shows **Results engine: json-v1** and the backend start time; new launches are disabled if an older process is detected. Exact processing steps and formulas are in [docs/results-pipeline.md](docs/results-pipeline.md).

Fresh `opp_env` launches remain restricted by the sandbox's Git/flake context. Local HTTP server sockets and Chrome are also blocked here, so browser interaction/screenshots and a fresh native simulation still require verification on the working host. The actual saved Cmdenv outputs were used to verify recovery; they were not synthetic results.

On a working host, complete acceptance by running the automotive zonal template, checking its emitted VLAN/PCP and actual queue assignment, then the other research families. Verify that source/sink vectors appear in the official JSON export and inspect CBS credit/throughput behavior before using results in the thesis. The cascaded-bottleneck template intentionally exceeds reservations and physical capacity on some ports. These runtime checks are necessary beyond syntax validation; nominal load checks do not establish loss or latency guarantees.

The implementation follows [the research implementation plan](docs/scenario-design-implementation-plan.md), updated to Vue and Cmdenv. The later feature profiles in that document are a roadmap, not claims of current support.

Restart the Python backend after updating the compiler; new runs require **Ethernet compiler 3.3**. Existing run bundles are retained. Host-side runtime checks are optional in the regular suite:

```bash
TSN_NATIVE_TESTS=1 PYTHONPATH=backend python3 -m pytest -q -k native_execution
```

Run this inside a working `opp_env` environment. It checks the two-switch regression, burst/drift generation, the supplied automotive multicast reference, and the new complex automotive scenario, including native result extraction and link occupancy. Small legacy scenarios live in `tests/fixtures/legacy-scenarios/`; the supplied reference is preserved in `scenarios/reference/`. Neither appears in the template menu. These runtime checks have not run successfully in the sandbox.
