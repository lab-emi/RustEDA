# SPDX-License-Identifier: Apache-2.0
"""The connectivity graph of the native ECO planner (native_ecos.Graph) and its bit labels: the
same rows, pins and budgets as the bit-by-bit construction, whatever the bus shape."""
from types import SimpleNamespace
import unittest

from integration.ibex_sg13g2_ez130 import native_ecos as ecos


def reference_labels(name, definition):
    # The bit-by-bit labelling that bit_labels must reproduce.
    bits = definition['bits']
    for index, bit in enumerate(bits):
        offset = definition.get('offset', 0)
        position = offset + (len(bits)-1-index if definition.get('upto', 0) else index)
        bus = definition.get('is_bus', len(bits) > 1 or offset != 0 or definition.get('upto', 0))
        yield (f'{name}[{position}]' if bus else name), bit


MODELS = {('BUF', 'A'): dict(cap=0.002, limit=None, fanout=1.0, clock=False, direction='Input', max_fanout=float('inf')),
          ('BUF', 'Y'): dict(cap=None, limit=0.1, fanout=0.0, clock=False, direction='Output', max_fanout=8.0),
          ('DFF', 'CK'): dict(cap=0.003, limit=None, fanout=1.0, clock=True, direction='Input', max_fanout=float('inf')),
          ('DFF', 'D'): dict(cap=0.002, limit=None, fanout=1.0, clock=False, direction='Input', max_fanout=float('inf')),
          ('DFF', 'Q'): dict(cap=None, limit=0.1, fanout=0.0, clock=False, direction='Output', max_fanout=8.0)}


def document():
    ports = lambda *names: {n: dict(bits=[0]) for n in names}
    cells = {
        'b1': dict(type='BUF', connections=dict(A=[2], Y=[3]), port_directions=dict(A='input', Y='output')),
        'b2': dict(type='BUF', connections=dict(A=['0'], Y=[4]), port_directions=dict(A='input', Y='output')),
        'r1': dict(type='DFF', connections=dict(CK=[5], D=[3], Q=[6]), port_directions=dict(CK='input', D='input', Q='output')),
        'r2': dict(type='DFF', connections=dict(CK=[5], D=[4], Q=[2]), port_directions=dict(CK='input', D='input', Q='output')),
    }
    top = dict(cells=cells, ports=dict(clk=dict(direction='input', bits=[5]), out=dict(direction='output', bits=[6])),
               netnames=dict(clk=dict(bits=[5]), n=dict(bits=[2, 3, 4], offset=1), q=dict(bits=[6], is_bus=1), u=dict(bits=[2, 3], upto=1)))
    return dict(modules=dict(top=top, BUF=dict(ports=ports('A', 'Y')), DFF=dict(ports=ports('CK', 'D', 'Q'))))


def inputs(max_connections=100, max_work=1000):
    state = SimpleNamespace(work=0, args=SimpleNamespace(max_cells=100, max_connections=max_connections, max_work=max_work))
    def tick(count=1):
        state.work += count
        ecos.require(state.work <= max_work, 'BUDGET', 'planner work budget exceeded')
    state.tick = tick
    return state


IO = dict(drivers=dict(clk=dict(cell='BUF', pin='Y')), loads_pf=dict(out=0.01))


class Labels(unittest.TestCase):
    def test_bit_labels_match_the_bit_by_bit_labelling(self):
        for definition in [dict(bits=[1]), dict(bits=[1, 2, 3]), dict(bits=[1, 2], offset=4), dict(bits=[1, 2, 3], upto=1),
                           dict(bits=[1, 2], offset=2, upto=1), dict(bits=[1], is_bus=1), dict(bits=[1, 2], is_bus=0),
                           dict(bits=[1], offset=3), dict(bits=[])]:
            self.assertEqual(list(ecos.bit_labels('p', definition)), list(reference_labels('p', definition)), definition)


class GraphBuild(unittest.TestCase):
    def test_rows_pins_and_work(self):
        state = inputs()
        g = ecos.Graph(state, document(), 'top', lambda cell, pin: dict(MODELS[cell, pin]), IO)
        self.assertEqual(state.work, 10)  # one tick per connection bit, constants included
        self.assertEqual(g.drivers[3], dict(instance='b1', pin='Y', cell='BUF', **MODELS['BUF', 'Y']))
        self.assertEqual(list(g.drivers), [3, 4, 6, 2, 5])
        self.assertEqual([(r['instance'], r['pin']) for r in g.loads[5]], [('r1', 'CK'), ('r2', 'CK')])
        self.assertEqual(g.loads[6], [dict(instance=None, pin=None, port='out', cell=None, cap=0.01, fanout=0.0, max_fanout=float('inf'), clock=False)])
        self.assertEqual(list(g.loads[5][0]), ['instance', 'pin', 'cell', 'cap', 'limit', 'fanout', 'clock', 'direction', 'max_fanout'])
        self.assertEqual(g.clock_bits, {5})
        self.assertNotIn(('b2', 'A'), g.pin_bits)
        self.assertEqual(g.net_bits, {'clk': 5, 'n[1]': 2, 'n[2]': 3, 'n[3]': 4, 'q[0]': 6, 'u[1]': 2, 'u[0]': 3})
        # Rows are separate objects.
        self.assertIsNot(g.loads[5][0], g.loads[5][1])

    def test_budgets_fail_as_before(self):
        pins = lambda cell, pin: dict(MODELS[cell, pin])
        with self.assertRaisesRegex(ecos.Failure, 'connection budget exceeded'):
            ecos.Graph(inputs(max_connections=9), document(), 'top', pins, IO)
        with self.assertRaisesRegex(ecos.Failure, 'planner work budget exceeded'):
            ecos.Graph(inputs(max_work=9), document(), 'top', pins, IO)
        state = inputs(max_connections=10, max_work=10)
        ecos.Graph(state, document(), 'top', pins, IO)
        self.assertEqual(state.work, 10)

    def test_a_wrong_pin_direction_is_rejected(self):
        doc = document(); doc['modules']['top']['cells']['b1']['port_directions']['Y'] = 'input'
        with self.assertRaisesRegex(ecos.Failure, 'direction mismatch'):
            ecos.Graph(inputs(), doc, 'top', lambda cell, pin: dict(MODELS[cell, pin]), IO)


if __name__ == '__main__':
    unittest.main()
