"""Recover a finished job using its frozen scenario and native outputs."""
import argparse
import time
from . import runner


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    args=parser.parse_args()
    runner.init()
    runner.retry_extraction(args.job_id)
    while runner.job(args.job_id)['status'] in ['queued','exporting','extracting']:
        time.sleep(.1)
    row=runner.job(args.job_id)
    print(row['status'],row['error'] or '')
    if row['status']!='completed': raise SystemExit(1)


if __name__=='__main__': main()
