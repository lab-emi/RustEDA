# SPDX-License-Identifier: Apache-2.0
"""KLayout batch driver: several tap-coverage checker sources on many small layouts.

usage: klayout -b -r driver.py -rd cases=CASES.json -rd out=OUT.json -rd srcs=NAME=PATH,NAME=PATH
CASES.json is [{name, gds, top, excluded}]. For every case and checker the output holds the verdict,
the diagnostic of a refused layout, the per-rule counts and every certify_box call as
[lx, ly, ux, uy, tie-point set id, accepted], straight-line and in-well separately, with the tie-point
sets listed once under `sets`. Calls are what the certificate actually examined, so two checkers
that list the same calls certified the same boxes against the same tie points.
"""
import hashlib
import json

LIMITS = dict(cells=16384, hierarchy_edges=250000, instance_visits=4000000, input_shapes=2000000,
              vertices=8000000, shape_visits=8000000, vertex_visits=32000000, domains=4096, anchors=250000,
              bin_entries=1000000, candidates=4096, boxes=16000000, distance_checks=200000000, pending=64, depth=32)


def load(path):
    namespace = {'__name__': 'checker', '__file__': path}
    with open(path) as stream:
        exec(compile(stream.read(), path, 'exec'), namespace)
    return namespace


def run_one(namespace, case):
    calls, sets, keys, keep = dict(straight=[], inwell=[]), {}, {}, []
    original = namespace['certify_box']

    def traced(box, points, budget, corridor=None):
        key = keys.get(id(points))
        if key is None:
            keep.append(points)  # keeps id() unique for the whole run
            ordered = sorted(point for cell in points.bins.values() for point in cell)
            key = keys[id(points)] = hashlib.sha256(json.dumps(ordered).encode()).hexdigest()[:16]
            sets[key] = ordered
        result = original(box, points, budget) if corridor is None else original(box, points, budget, corridor)
        calls['straight' if corridor is None else 'inwell'].append(list(box) + [key, result is None])
        return result

    namespace['certify_box'] = traced
    config = dict(gds=dict(path=case['gds']), top=case['top'], excluded_cells=case['excluded'], limits=dict(LIMITS))
    try:
        report = namespace['geometry_check'](config, namespace['Budget'](config['limits']))
        row = dict(status=report['status'], diagnostic=None,
                   rules={rule: dict(status=entry['status'], domains=entry['domains'], targets=entry['target_polygons'],
                                     ties=entry['tie_points']) for rule, entry in report['rules'].items()})
    except ValueError as error:  # run() reports these as unverified with the diagnostic
        row = dict(status='unverified', diagnostic=str(error), rules={})
    finally:
        namespace['certify_box'] = original
    row.update(calls=calls, sets=sets)
    return row


def main():
    loaded = [(name, load(path)) for name, path in (item.split('=', 1) for item in srcs.split(','))]  # noqa: F821
    with open(cases) as stream:  # noqa: F821
        todo = json.load(stream)
    results = []
    for case in todo:
        row = dict(name=case['name'])
        for name, namespace in loaded:
            row[name] = run_one(namespace, case)
        results.append(row)
    with open(out, 'w') as stream:  # noqa: F821
        json.dump(results, stream)


main()
