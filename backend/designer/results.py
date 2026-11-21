"""Official result export and readers; metric definitions live in metrics.py."""
import csv
import json
import math
from pathlib import Path

PIPELINE_VERSION = 'json-v1'
LEGACY_CSV_FIELD_LIMIT = 256 * 1024 * 1024


def export_filter(manifest):
    """Export packet observations and selected queue diagnostics, not every signal."""
    expressions = []
    if manifest.get('measurement_version', 1) >= 3:
        for modules in manifest['flow_modules'].values():
            expressions.append(f'(module =~ {json.dumps(modules["source"])} AND (name =~ "packetLengths:vector" OR name =~ "packets:count"))')
            for receiver in modules['receivers'].values():
                expressions.append(f'(module =~ {json.dumps(receiver["recorder"])} AND (name =~ "*:mean*:vector" OR name =~ "*:packetJitter:vector" OR name =~ "*:bitElapsedTimePerRegion:vector" OR name =~ "*:flowDataRate:vector"))')
                expressions.append(f'(module =~ {json.dumps(receiver["sink"])} AND name =~ "packets:count")')
        expressions.append('(module =~ "**.macLayer.queue.**" AND (name =~ "queueLength:**" OR name =~ "queueingTime:vector" OR name =~ "numCredits:vector" OR name =~ "droppedPacketsQueueOverflow:count"))')
        expressions.append('(name =~ "busy:vector")')
        if manifest.get('link_occupancy',{}).get('enabled'):
            expressions.append('(module =~ "**.phyLayer.transmitter" AND (name =~ "transmitting:vector" OR name =~ "*:flowTransmissionTime:vector" OR name =~ "propagationTime"))')
        return ' OR '.join(dict.fromkeys(expressions))
    for modules in manifest['flow_modules'].values():
        for role, name in [('source', 'packetLengths:vector'), ('sink', 'packetLifeTime:vector')]:
            names=[name,'packets:count']
            if modules.get(role+'_sequence'): names.append(modules[role+'_sequence'])
            selected=' OR '.join('name =~ '+json.dumps(n) for n in names)
            expressions.append(f'(module =~ {json.dumps(modules[role])} AND ({selected}))')
    expressions.append('(module =~ "**.macLayer.queue.**" AND (name =~ "queueLength:**" OR name =~ "queueingTime:vector" OR name =~ "numCredits:vector" OR name =~ "droppedPacketsQueueOverflow:count"))')
    return ' OR '.join(expressions)


def export_command(tool, inputs, output, manifest):
    return [str(tool), 'export', '-F', 'JSON', '-T', 'sv', '-f', export_filter(manifest),
            '-x', 'pythonFlavoured=false', '-x', 'indentSize=0', '-x', 'precision=15',
            '-o', str(output), *map(str, inputs)]


def read_results(path):
    """Return one run's vectors and scalars with the same metric input contract."""
    path = Path(path)
    vectors, scalars = {}, []
    if path.suffix == '.csv':
        # Compatibility only. Set this at the point of use, even if another
        # library or test reset the process-global default after our import.
        csv.field_size_limit(max(csv.field_size_limit(), LEGACY_CSV_FIELD_LIMIT))
        with path.open(newline='') as stream:
            for row in csv.DictReader(stream):
                key = (row.get('module'), row.get('name'))
                if row.get('type') == 'vector':
                    times = [float(v) for v in (row.get('vectime') or '').split()]
                    values = [float(v) for v in (row.get('vecvalue') or '').split()]
                    _add_vector(vectors, key, times, values)
                elif row.get('type') == 'scalar':
                    _add_scalar(scalars, key, row.get('value'))
    else:
        with path.open() as stream:
            runs = json.load(stream)
        if not isinstance(runs, dict) or len(runs) != 1:
            raise ValueError('Expected exactly one OMNeT++ result run; cannot combine seeds silently')
        run = next(iter(runs.values()))
        for row in run.get('vectors', []):
            _add_vector(vectors, (row['module'], row['name']), row['time'], row['value'])
        for row in run.get('scalars', []):
            _add_scalar(scalars, (row['module'], row['name']), row.get('value'))
    return vectors, scalars


def _add_vector(vectors, key, times, values):
    if len(times) != len(values):
        raise ValueError(f'Malformed exported vector: {key}')
    if key in vectors:
        raise ValueError(f'Duplicate result vector: {key}')
    vectors[key] = list(zip(times, values))


def _add_scalar(scalars, key, value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return
    if math.isfinite(number):
        scalars.append(dict(module=key[0], name=key[1], value=number))
