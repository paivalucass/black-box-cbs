import argparse
import json
from pathlib import Path
from .schema import Scenario
from .compiler import compile_scenario

def main():
    parser=argparse.ArgumentParser(description='Validate, generate or run a TSN scenario')
    parser.add_argument('action',choices=['validate','generate','run','extract'])
    parser.add_argument('scenario',type=Path)
    parser.add_argument('--output',type=Path,default=Path('var/export'))
    parser.add_argument('--results',type=Path,help='Official JSON export or legacy CSV-R export')
    parser.add_argument('--manifest',type=Path,help='Frozen run manifest; automatically found beside the results when available')
    parser.add_argument('--csv',type=Path,help='Legacy alias for --results')
    args=parser.parse_args()
    s=Scenario.model_validate_json(args.scenario.read_text())
    result=compile_scenario(s) if args.action!='extract' else None
    if args.action=='validate': print(json.dumps(result['diagnostics'],indent=2))
    elif args.action=='generate':
        for name,text in result['files'].items():
            p=args.output/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text)
        print(args.output.resolve())
    elif args.action=='extract':
        source=args.results or args.csv
        if not source: parser.error('extract requires --results exported by opp_scavetool')
        from .metrics import extract
        manifest_path=args.manifest or next((p for p in [source.parent/'manifest.json',source.parent.parent/'manifest.json'] if p.exists()),None)
        manifest=json.loads(manifest_path.read_text()) if manifest_path else compile_scenario(s)['manifest']
        print(json.dumps(extract(source,s,manifest),indent=2))
    else:
        from . import runner
        import time
        runner.init(); id=runner.submit(s); print(id,flush=True)
        while runner.job(id)['status'] in ['queued','validating','running','exporting','extracting']: time.sleep(.2)
        row=runner.job(id); print(row['status'],row['error'] or '')
        if row['status']!='completed': raise SystemExit(1)

if __name__=='__main__': main()
