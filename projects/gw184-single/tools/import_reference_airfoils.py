"""Extract only documented coordinate arrays from a downloaded pinned WISDEM YAML.
Usage: python3 tools/import_reference_airfoils.py SOURCE_YAML OUTPUT_JSON
No YAML code execution, network, solver, or turbine-model import.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
source=Path(sys.argv[1])
text=source.read_text().split('\nairfoils:\n',1)[1].split('\nmaterials:',1)[0]
profiles=[]
for section in re.split(r'\n   -  name: ',text):
    name=section.splitlines()[0].strip().replace('-  name: ','')
    if name not in ('FFA-W3-241','FFA-W3-301','FFA-W3-360'):
        continue
    x=ast.literal_eval(re.search(r'^\s+x: (\[.*?\])',section,re.M).group(1))
    y=ast.literal_eval(re.search(r'^\s+y: (\[.*?\])',section,re.M).group(1))
    thickness=float(re.search(r'^\s+rthick: (.*)',section,re.M).group(1))
    assert len(x)==len(y)>100
    profiles.append(dict(name=name,relative_thickness=thickness,coordinates=list(map(list,zip(x,y)))))
assert len(profiles)==3
result=dict(schema='gw184.ffa-reference.v1',source='NLRWindSystems/WISDEM examples/02_reference_turbines/IEA-15-240-RWT.yaml',
    commit='bc964bee6e323f6a12d559191470d680da7fd95b',
    url='https://github.com/NLRWindSystems/WISDEM/blob/bc964bee6e323f6a12d559191470d680da7fd95b/examples/02_reference_turbines/IEA-15-240-RWT.yaml',
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),retrieved='2026-09-29',
    scope='Only FFA-W3-241/301/360 geometric contours; no IEA-15MW dimensions or polars imported; adapted reference not exact DTU/GW184 surface',
    profiles=sorted(profiles,key=lambda r:r['relative_thickness']))
Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n')
print([(p['name'],p['relative_thickness'],len(p['coordinates'])) for p in profiles])
