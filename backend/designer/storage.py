"""Named simulations, separate from bundled read-only templates and run snapshots."""
import json
import time
import uuid
from . import runner
from .schema import Scenario

class NameConflict(ValueError):
    pass

def record(row):
    r=dict(row)
    r.pop('name_key',None)
    r['scenario']=Scenario.model_validate_json(r.pop('body')).model_dump()
    return r

def list_models():
    with runner.connect() as db:
        return [record(r) for r in db.execute('SELECT * FROM simulation_models ORDER BY updated DESC')]

def get_model(id):
    with runner.connect() as db:
        row=db.execute('SELECT * FROM simulation_models WHERE id=?',(id,)).fetchone()
    if row is None: raise KeyError(id)
    return record(row)

def save_model(s: Scenario, id=None):
    name=s.name.strip()
    if not name: raise ValueError('Enter a simulation name')
    s=s.model_copy(update={'name':name})
    now=time.time()
    with runner.connect() as db:
        if id is not None and not db.execute('SELECT id FROM simulation_models WHERE id=?',(id,)).fetchone(): raise KeyError(id)
        duplicate=db.execute('SELECT id FROM simulation_models WHERE name_key=?',(name.casefold(),)).fetchone()
        if duplicate and duplicate['id']!=id:
            raise NameConflict('A simulation with this name already exists. Open it to update it, or choose a different name.')
        if id is None:
            id=uuid.uuid4().hex
            db.execute('INSERT INTO simulation_models VALUES (?,?,?,?,?,?)',(id,name,name.casefold(),now,now,s.model_dump_json()))
        else:
            db.execute('UPDATE simulation_models SET name=?,name_key=?,updated=?,body=? WHERE id=?',(name,name.casefold(),now,s.model_dump_json(),id))
    return get_model(id)
