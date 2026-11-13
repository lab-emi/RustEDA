# SPDX-License-Identifier: Apache-2.0
"""Latch-up control layouts for the tap-coverage certificate, as GDSII bytes (pure Python).

Three parts: a minimal deterministic GDSII stream writer, a table of controls with one feature
under test each (CONTROLS), and a seeded generator of random hierarchical layouts (random_layout).
Coordinates are in um on IHP SG13G2 drawing layers, 1 DBU = 1 nm.

A layout is {cell name: [element, ...]}; elements are tuples:
  ('box', layer, x0, y0, x1, y1)                     BOUNDARY rectangle
  ('poly', layer, [(x, y), ...])                     BOUNDARY polygon
  ('path', layer, [(x, y), ...], width)              PATH
  ('text', layer, x, y, string)                      TEXT
  ('gbox', layer, x0, y0, x1, y1)                    GDS BOX record
  ('pbox', ...), ('ppoly', ...), ('ppath', ...)      the same with a user property
  ('ref', child, x, y, angle, mirror[, mag])         SREF; mirror about the x axis before rotation
  ('aref', child, x, y, angle, mirror, cols, rows, dx, dy)   AREF with orthogonal pitches
`layer` is (layer, datatype).

Every control carries a passing LU.a pair and a passing LU.b pair unless that rule is the feature
under test, so a failure can only come from the feature. `expect` is the verdict of the current
checker ('passed', 'unverified' or the diagnostic of a refused layout); `expect_old` is the verdict
of the checker of 2026-11-05 where it differs.
"""
import random
import struct

LAYERS = dict(active=(1, 0), psd=(14, 0), nsd=(7, 0), nsd_block=(7, 21), nwell=(31, 0),
              pwell_block=(46, 21), pwell_draw=(46, 0), contact=(6, 0))


def dbu(value):
    return int(round(value * 1000))


def _record(kind, data=b''):
    if len(data) % 2:
        data += b'\0'
    return struct.pack('>HH', len(data) + 4, kind) + data


def _i2(*values):
    return struct.pack('>%dh' % len(values), *values)


def _i4(*values):
    return struct.pack('>%di' % len(values), *values)


def _r8(value):
    if value == 0:
        return b'\0' * 8
    sign, value, exponent = (0x80 if value < 0 else 0), abs(value), 64
    while value >= 1:
        value, exponent = value / 16, exponent + 1
    while value < 1 / 16:
        value, exponent = value * 16, exponent - 1
    return bytes([sign | exponent]) + int(value * (1 << 56)).to_bytes(7, 'big')


def _xy(points):
    return _record(0x1003, _i4(*[dbu(c) for point in points for c in point]))


def _strans(angle, mirror, mag=1.0):
    out = _record(0x1A01, _i2(-0x8000 if mirror else 0))
    if mag != 1.0:
        out += _record(0x1B05, _r8(mag))
    if angle:
        out += _record(0x1C05, _r8(float(angle)))
    return out


def gds_bytes(cells, libname='controls'):
    stamp = _i2(*([2026, 1, 1, 0, 0, 0] * 2))
    out = [_record(0x0002, _i2(600)), _record(0x0102, stamp), _record(0x0206, libname.encode()),
           _record(0x0305, _r8(1e-3) + _r8(1e-9))]
    for name, elements in cells.items():
        out += [_record(0x0502, stamp), _record(0x0606, name.encode())]
        for element in elements:
            kind = element[0]
            if kind in ('box', 'pbox', 'gbox', 'poly', 'ppoly'):
                if kind in ('poly', 'ppoly'):
                    layer, points = element[1], list(element[2])
                else:
                    layer, (x0, y0, x1, y1) = element[1], element[2:6]
                    points = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
                out += [_record(0x2D00 if kind == 'gbox' else 0x0800), _record(0x0D02, _i2(layer[0])),
                        _record(0x2E02 if kind == 'gbox' else 0x0E02, _i2(layer[1])), _xy(points + points[:1])]
                if kind in ('pbox', 'ppoly'):
                    out += [_record(0x2B02, _i2(1)), _record(0x2C06, b'control')]
            elif kind in ('path', 'ppath'):
                layer, points, width = element[1:4]
                out += [_record(0x0900), _record(0x0D02, _i2(layer[0])), _record(0x0E02, _i2(layer[1])),
                        _record(0x0F03, _i4(dbu(width))), _xy(points)]
                if kind == 'ppath':
                    out += [_record(0x2B02, _i2(1)), _record(0x2C06, b'control')]
            elif kind == 'text':
                layer, x, y, string = element[1:5]
                out += [_record(0x0C00), _record(0x0D02, _i2(layer[0])), _record(0x1602, _i2(layer[1])),
                        _xy([(x, y)]), _record(0x1906, string.encode())]
            elif kind == 'ref':
                child, x, y, angle, mirror = element[1:6]
                mag = element[6] if len(element) > 6 else 1.0
                out += [_record(0x0A00), _record(0x1206, child.encode()), _strans(angle, mirror, mag), _xy([(x, y)])]
            elif kind == 'aref':
                child, x, y, angle, mirror, cols, rows, dx, dy = element[1:10]
                # Column and row vectors rotate with the instance (KLayout reads them from the XY triple).
                ca, sa = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle % 360]
                col = (x + cols * dx * ca, y + cols * dx * sa)
                row = (x - rows * dy * sa, y + rows * dy * ca)
                out += [_record(0x0B00), _record(0x1206, child.encode()), _strans(angle, mirror),
                        _record(0x1302, _i2(cols, rows)), _xy([(x, y), col, row])]
            else:
                raise ValueError(kind)
            out.append(_record(0x1100))
        out.append(_record(0x0700))
    out.append(_record(0x0400))
    return b''.join(out)


TOP = 'ibex_sram_bench'
MACRO = 'RM_IHPSG13_1P_1024x32_c2_bm_bist'
ACT, PSD, NSD, NSDB = LAYERS['active'], LAYERS['psd'], LAYERS['nsd'], LAYERS['nsd_block']
NW, PWB, PWD, CONT = LAYERS['nwell'], LAYERS['pwell_block'], LAYERS['pwell_draw'], LAYERS['contact']
METAL1 = (8, 0)


def box(layer, x0, y0, x1, y1):
    return ('box', layer, x0, y0, x1, y1)


def n_active(x, y, w=1.0, h=1.0):          # N+ Activ: Activ without pSD
    return [box(ACT, x, y, x + w, y + h)]


def p_active(x, y, w=1.0, h=0.5):          # P+ Activ: Activ under pSD
    return [box(ACT, x, y, x + w, y + h), box(PSD, x - 0.2, y - 0.2, x + w + 0.2, y + h + 0.2)]


def cont(x, y):                            # 0.16 um contact with its lower-left corner at (x, y)
    return [box(CONT, x, y, x + 0.16, y + 0.16)]


def n_tie(x, y):                           # contacted N+ Activ (an n-well tie when inside NWell)
    return n_active(x, y) + cont(x + 0.4, y + 0.4)


def p_tie(x, y):                           # contacted P+ Activ (a substrate tie outside NWell)
    return p_active(x, y, 1.0, 1.0) + cont(x + 0.4, y + 0.4)


def lub_pair(x=100.0, y=100.0):            # passing LU.b: N+ target 5 um from a substrate tie
    return p_tie(x, y) + n_active(x + 5, y, 1.0, 0.5)


def lua_pair(x=0.0, y=0.0):                # passing LU.a: P+ target 4 um from an n-well tie
    return [box(NW, x, y, x + 30, y + 6)] + n_tie(x + 1, y + 1) + p_active(x + 5, y + 2.5)


def far_pair():
    """A second n-well domain and a second substrate domain (an island inside an n-well ring),
    each with its own passing pair: every per-domain code path then sees more than one domain."""
    x, y = 300.0, 300.0
    ring = [box(NW, x, y, x + 30, y + 4), box(NW, x, y + 16, x + 30, y + 20),
            box(NW, x, y, x + 4, y + 20), box(NW, x + 26, y, x + 30, y + 20)]
    return ring + n_tie(x + 1, y + 1) + p_active(x + 6, y + 1.5) + p_tie(x + 8, y + 8) + n_active(x + 12, y + 8, 1.0, 0.5)


CONTROLS = {}


def control(name, expect, note, excluded=False, old=None, old_far=None):
    """`old`: verdict of the checker of 2026-11-05 where it differs; `old_far`: the same for the
    variant with far_pair() where that differs again."""
    def register(build):
        expect_old = expect if old is None else old
        CONTROLS[name] = dict(build=build, expect=expect, note=note, excluded=excluded, expect_old=expect_old,
                              expect_old_far=expect_old if old_far is None else old_far)
        return build
    return register


def layout(name, far=False):
    """cells of control `name`; far=True adds far_pair() to the top cell."""
    cells = CONTROLS[name]['build']()
    cells = {cell: list(elements) for cell, elements in cells.items()}
    if far:
        cells[TOP] = cells[TOP] + far_pair()
    if name != 'refuse_two_tops':  # helper cells that a control does not place would be extra top cells
        used, stack = set(), [TOP]
        while stack:
            cell = stack.pop()
            if cell not in used:
                used.add(cell)
                stack += [element[1] for element in cells.get(cell, ()) if element[0] in ('ref', 'aref')]
        cells = {cell: elements for cell, elements in cells.items() if cell in used}
    return cells


# ---------------------------------------------------------------- baseline and distance boundary
@control('legal', 'passed', 'legal pair for each rule, all shapes at top level')
def _():
    return {TOP: lua_pair() + lub_pair()}


def lua_boundary(right):
    # tie point (1.4, 1.4); target [right-1, right] x [2.5, 3.0]: farthest corner L1 = right + 0.2
    return {TOP: [box(NW, 0, 0, 40, 6)] + n_tie(1, 1) + p_active(right - 1, 2.5) + lub_pair()}


def lub_boundary(right):
    return {TOP: lua_pair() + p_tie(100, 100) + n_active(100 + right - 1, 100.5, 1.0, 0.5)}


@control('lua_on_limit', 'passed', 'LU.a: farthest target corner exactly 20.000 um (L1) from the tie point')
def _():
    return lua_boundary(19.8)


@control('lua_inside_limit', 'passed', 'LU.a: farthest target corner 19.999 um from the tie point')
def _():
    return lua_boundary(19.799)


@control('lua_outside_limit', 'unverified', 'LU.a: farthest target corner 20.001 um from the tie point')
def _():
    return lua_boundary(19.801)


@control('lub_on_limit', 'passed', 'LU.b: farthest target corner exactly 20.000 um (L1) from the tie point')
def _():
    return lub_boundary(19.8)


@control('lub_inside_limit', 'passed', 'LU.b: farthest target corner 19.999 um from the tie point')
def _():
    return lub_boundary(19.799)


@control('lub_outside_limit', 'unverified', 'LU.b: farthest target corner 20.001 um from the tie point')
def _():
    return lub_boundary(19.801)


@control('lua_two_ties_cover', 'passed', 'LU.a: a 37.2 um long target covered only by the union of two ties, midpoint on the limit')
def _():
    # tie points (1.4, 1.4) and (38.6, 1.4); the target's top edge is 1.4 um above them: midpoint exactly 20.000 um
    return {TOP: [box(NW, 0, 0, 60, 6)] + n_tie(1, 1) + n_tie(38.2, 1) + p_active(1.4, 2.5, 37.2, 0.3) + lub_pair()}


@control('lua_two_ties_gap', 'unverified',
         'LU.a: the second tie 1 nm further: both integer midpoints are in range, the half-nanometre point between them is not')
def _():
    return {TOP: [box(NW, 0, 0, 60, 6)] + n_tie(1, 1) + n_tie(38.201, 1) + p_active(1.4, 2.5, 37.201, 0.3) + lub_pair()}


# ---------------------------------------------------------------- missing ties, wrong domain
@control('lua_no_tie', 'unverified', 'LU.a: n-well with a target and an uncontacted N+ Activ')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + p_active(5, 2.5) + lub_pair()}


@control('lub_no_tie', 'unverified', 'LU.b: substrate target with an uncontacted P+ Activ')
def _():
    return {TOP: lua_pair() + p_active(100, 100, 1.0, 1.0) + n_active(105, 100, 1.0, 0.5)}


@control('lua_tie_other_well', 'unverified', 'LU.a: target in a strip without ties, 6.72 um from a tied strip')
def _():
    return {TOP: [box(NW, 0, 0, 60, 4), box(NW, 0, 10.72, 60, 14.72)] + n_tie(30, 1.5) + p_active(35, 1.5)
            + p_active(30, 12) + lub_pair()}


@control('lub_island', 'unverified', 'LU.b: N+ target in a substrate island inside an n-well ring, tie outside')
def _():
    ring = [box(NW, 50, 50, 70, 52), box(NW, 50, 68, 70, 70), box(NW, 50, 50, 52, 70), box(NW, 68, 50, 70, 70)]
    return {TOP: ring + n_tie(60, 50.5) + p_active(55, 50.75) + n_active(59, 59, 1.0, 0.5) + p_tie(59, 45)
            + n_active(62, 45, 1.0, 0.5)}


@control('lua_tie_under_psd', 'unverified', 'LU.a: the only contacted Activ of the well lies under pSD (not an n-tie)')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_tie(1, 1) + [box(PSD, 0.8, 0.8, 2.2, 2.2)] + p_active(5, 2.5) + lub_pair()}


@control('lua_tie_under_nsd_block', 'unverified', 'LU.a: the only contacted Activ lies under nSD:block')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_tie(1, 1) + [box(NSDB, 0.8, 0.8, 2.2, 2.2)] + p_active(5, 2.5) + lub_pair()}


@control('lua_tie_nsd_over_block', 'passed', 'LU.a: drawn nSD restores the tie under nSD:block')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_tie(1, 1) + [box(NSDB, 0.8, 0.8, 2.2, 2.2), box(NSD, 0.9, 0.9, 2.1, 2.1)]
            + p_active(5, 2.5) + lub_pair()}


@control('lub_tie_in_pwell_block', 'unverified', 'LU.b: the only substrate tie lies in PWell:block')
def _():
    return {TOP: lua_pair() + lub_pair() + [box(PWB, 99, 99, 102, 102)]}


@control('lub_tie_pwell_draw', 'passed', 'LU.b: drawn PWell restores the domain under PWell:block')
def _():
    return {TOP: lua_pair() + lub_pair() + [box(PWB, 99, 99, 102, 102), box(PWD, 99, 99, 102, 102)]}


# ---------------------------------------------------------------- contacts and tie diffusion
@control('contact_abuts_tie', 'unverified', 'LU.a: the contact only abuts the tie Activ edge (no common area)')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + cont(2.0, 1.4) + p_active(5, 2.5) + lub_pair()}


@control('contact_corner_tie', 'unverified', 'LU.a: the contact touches the tie Activ at one corner only')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + cont(2.0, 2.0) + p_active(5, 2.5) + lub_pair()}


@control('contact_partly_on_tie', 'passed', 'LU.a: half of the contact lies on the tie Activ')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + cont(1.92, 1.4) + p_active(5, 2.5) + lub_pair()}


@control('contact_1dbu_on_tie', 'passed', 'LU.a: a 1 nm wide strip of the contact lies on the tie Activ')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + cont(1.999, 1.4) + p_active(5, 2.5) + lub_pair()}


@control('contact_partly_limit', 'unverified',
         'LU.a: the tie point is the corner of the contact part on the Activ, not of the whole contact: 20.001 um')
def _():
    # contact x 0.92..1.08, Activ from x=1: the contacted tie is x 1..1.08, tie point (1.0, 1.4), not (0.92, 1.4).
    # Target on the left, x -17.401..-16.401, top 3.0: farthest corner 18.401 + 1.6 from the tie point.
    return {TOP: [box(NW, -30, 0, 10, 6)] + n_active(1, 1) + cont(0.92, 1.4) + p_active(-17.401, 2.5) + lub_pair()}


@control('contact_partly_limit_on', 'passed', 'LU.a: the same, target 1 nm closer: exactly 20.000 um')
def _():
    return {TOP: [box(NW, -30, 0, 10, 6)] + n_active(1, 1) + cont(0.92, 1.4) + p_active(-17.4, 2.5) + lub_pair()}


@control('contact_on_butted_junction', 'unverified',
         'LU.a: contact straddles a butted N+/P+ junction; only its N+ part is a tie (20.001 um)')
def _():
    # Activ 1..3, pSD covers x<2: N+ part x 2..3. Contact x 1.92..2.08: tie point (2.0, 1.4), not (1.92, 1.4).
    # Target on the left, x -16.401..-15.401: farthest corner 18.401 + 1.6.
    return {TOP: [box(NW, -30, 0, 10, 6), box(ACT, 1, 1, 3, 2), box(PSD, 0.8, 0.8, 2, 2.2)] + cont(1.92, 1.4)
            + p_active(-16.401, 2.5) + lub_pair()}


@control('contact_outside_well', 'unverified', 'LU.a: the N+ Activ crosses the well edge, its contact lies outside the well')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6), box(ACT, 1, -1, 2, 2)] + cont(1.4, -0.6) + p_active(5, 2.5) + p_tie(5, -3) + lub_pair()}


@control('contact_on_well_edge', 'passed', 'LU.a: the contact crosses the well edge; its part inside the well is the tie')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6), box(ACT, 1, -1, 2, 2)] + cont(1.4, -0.08) + p_active(5, 2.5) + p_tie(5, -3) + lub_pair()}


# ---------------------------------------------------------------- hierarchy
def std_cells():
    return {'TIEN': n_tie(0, 0), 'TGTP': p_active(0, 0), 'TIEP': p_tie(0, 0), 'TGTN': n_active(0, 0, 1.0, 0.5)}


@control('hier_legal', 'passed', 'ties and targets in child cells, wells at top level')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 30, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'TGTP', 5, 2.5, 0, False),
                  ('ref', 'TIEP', 100, 100, 0, False), ('ref', 'TGTN', 105, 100, 0, False)]
    return cells


def oriented(angle, mirror):
    # ARM holds a target 19 um from its origin; the tie point sits 0.2 um from the origin. Only R0 keeps the
    # target inside the limit and inside the well; every other orientation moves it elsewhere.
    cells = dict(std_cells(), ARM=p_active(17.8, 1.0))
    cells[TOP] = [box(NW, -30, -30, 30, 30), ('ref', 'TIEN', -0.2, -0.2, 0, False),
                  ('ref', 'ARM', 0, 0, angle, mirror)] + lub_pair()
    return cells


for _angle, _mirror, _expect in ((0, False, 'passed'), (90, False, 'unverified'), (180, False, 'unverified'),
                                 (270, False, 'unverified'), (0, True, 'unverified'), (90, True, 'passed'),
                                 (180, True, 'unverified'), (270, True, 'unverified')):
    control('orient_r%d%s' % (_angle, 'm' if _mirror else ''), _expect,
            'target arm placed R%d%s: the farthest target corner is %s the limit'
            % (_angle, ' mirrored' if _mirror else '', 'inside' if _expect == 'passed' else 'outside'))(
        lambda a=_angle, m=_mirror: oriented(a, m))


@control('array_targets_reach', 'passed', 'AREF 15x1 of target cells, pitch 1.2 um: all within the limit')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 60, 6), ('ref', 'TIEN', 1, 1, 0, False),
                  ('aref', 'TGTP', 1.4, 2.5, 0, False, 15, 1, 1.2, 1.0)] + lub_pair()
    return cells


@control('array_targets_beyond', 'unverified', 'AREF 16x1 of target cells: the last one ends 20.6 um from the tie point')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 60, 6), ('ref', 'TIEN', 1, 1, 0, False),
                  ('aref', 'TGTP', 1.4, 2.5, 0, False, 16, 1, 1.2, 1.0)] + lub_pair()
    return cells


@control('array_ties_cover', 'passed', 'AREF 3x1 of tie cells, pitch 36 um, under a 104 um target')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 120, 6), ('aref', 'TIEN', 17, 1, 0, False, 3, 1, 36.0, 1.0),
                  box(ACT, 1, 2.5, 105, 2.8), box(PSD, 0.8, 2.3, 105.2, 3.0)] + lub_pair()
    return cells


@control('array_ties_short', 'unverified', 'AREF 2x1 of tie cells under the same target: its far end is uncovered')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 120, 6), ('aref', 'TIEN', 17, 1, 0, False, 2, 1, 36.0, 1.0),
                  box(ACT, 1, 2.5, 105, 2.8), box(PSD, 0.8, 2.3, 105.2, 3.0)] + lub_pair()
    return cells


def rotated_array(rows):
    cells = std_cells()
    # R90 array: column vector (0, +1.5), row vector (-1.7, 0); TGTP rotated covers x -0.5..0, y 0..1 of its origin.
    # Farthest corner of element (row r, column c) from the tie point (0.4, 0.4): 3.5 + 1.7 r + 1.5 c um.
    cells[TOP] = [box(NW, -60, -10, 30, 30), ('ref', 'TIEN', 0, 0, 0, False),
                  ('aref', 'TGTP', 0, 2, 90, False, 2, rows, 1.5, 1.7)] + lub_pair()
    return cells


@control('array_rotated_reach', 'passed', 'AREF 2x9 placed R90 (rows run along -x): the farthest element ends 19.6 um away')
def _():
    return rotated_array(9)


@control('array_rotated_beyond', 'unverified', 'AREF 2x10 placed R90: element (row 9, column 1) ends 20.3 um away')
def _():
    return rotated_array(10)


@control('nested_two_levels', 'unverified', 'target two levels down under a mirrored and a rotated parent')
def _():
    cells = dict(std_cells(), LEAF=p_active(18.3, 0.0), MID=[('ref', 'LEAF', 0, 0, 0, True)])
    # LEAF mirrored in MID: target y -0.5..0; MID placed R90: target x 0..0.5, y 18.3..19.3 around (0.2, 0.2).
    cells[TOP] = [box(NW, -30, -30, 30, 30), ('ref', 'TIEN', -0.2, -0.2, 0, False), ('ref', 'MID', 0, 0, 90, False),
                  ('ref', 'MID', 1.3, 0, 90, False)] + lub_pair()
    return cells


# ---------------------------------------------------------------- shapes that cross cell boundaries
@control('well_abutting_cells', 'passed', 'one n-well domain formed by abutting shapes of two cells; tie in one, target in the other')
def _():
    cells = dict(WA=[box(NW, 0, 0, 10, 6)] + n_tie(1, 1), WB=[box(NW, 0, 0, 10, 6)] + p_active(5, 2.5))
    cells[TOP] = [('ref', 'WA', 0, 0, 0, False), ('ref', 'WB', 10, 0, 0, False)] + lub_pair()
    return cells


@control('well_gap_cells', 'unverified', 'the same with a 1 nm gap between the two well shapes: two domains')
def _():
    cells = dict(WA=[box(NW, 0, 0, 10, 6)] + n_tie(1, 1), WB=[box(NW, 0, 0, 10, 6)] + p_active(5, 2.5))
    cells[TOP] = [('ref', 'WA', 0, 0, 0, False), ('ref', 'WB', 10.001, 0, 0, False)] + lub_pair()
    return cells


@control('well_corner_touch_cells', 'unverified', 'n-well shapes of two cells touch at one corner only: two domains')
def _():
    cells = dict(WA=[box(NW, 0, 0, 10, 6)] + n_tie(1, 1), WB=[box(NW, 0, 0, 10, 6)] + p_active(1, 2.5))
    cells[TOP] = [('ref', 'WA', 0, 0, 0, False), ('ref', 'WB', 10, 6, 0, False)] + lub_pair()
    return cells


@control('well_corner_touch_one_cell', 'unverified', 'n-well shapes of one cell touch at one corner only: two domains')
def _():
    cells = dict(WW=[box(NW, 0, 0, 10, 6), box(NW, 10, 6, 20, 12)] + n_tie(1, 1) + p_active(11, 8.5))
    cells[TOP] = [('ref', 'WW', 0, 0, 0, False)] + lub_pair()
    return cells


@control('well_corner_touch_top', 'unverified', 'top-level n-well shapes touch at one corner only: two domains')
def _():
    return {TOP: [box(NW, 0, 0, 10, 6), box(NW, 10, 6, 20, 12)] + n_tie(1, 1) + p_active(11, 8.5) + lub_pair()}


@control('well_bridge_from_parent', 'passed', 'two child wells joined by a top-level n-well shape')
def _():
    cells = dict(WA=[box(NW, 0, 0, 10, 6)] + n_tie(1, 1), WB=[box(NW, 0, 0, 10, 6)] + p_active(1, 2.5))
    cells[TOP] = [('ref', 'WA', 0, 0, 0, False), ('ref', 'WB', 12, 0, 0, False), box(NW, 10, 2, 12, 4)] + lub_pair()
    return cells


@control('tie_contact_other_cell', 'passed', 'the tie exists only flattened: contact in one cell, N+ Activ in another')
def _():
    cells = dict(CO=cont(0, 0), AC=n_active(0, 0))
    cells[TOP] = [box(NW, 0, 0, 30, 6), ('ref', 'AC', 1, 1, 0, False), ('ref', 'CO', 1.4, 1.4, 0, False)] \
        + p_active(5, 2.5) + lub_pair()
    return cells


@control('tie_across_cell_seam', 'unverified',
         'a contact across the seam of two abutting Activ cells is one tie with one point (its lower left); 20.001 um')
def _():
    # Activ halves x 1..1.5 and 1.5..2 from two instances; contact x 1.42..1.58: tie point (1.42, 1.4), not (1.5, 1.4)
    cells = dict(HALF=n_active(0, 0, 0.5, 1.0))
    cells[TOP] = [box(NW, 0, 0, 40, 6), ('ref', 'HALF', 1, 1, 0, False), ('ref', 'HALF', 1.5, 1, 0, False)] \
        + cont(1.42, 1.4) + p_active(18.821, 2.5) + lub_pair()
    return cells


@control('tie_contact_other_cell_limit', 'unverified', 'the same, target 20.001 um from that tie point')
def _():
    cells = dict(CO=cont(0, 0), AC=n_active(0, 0))
    cells[TOP] = [box(NW, 0, 0, 40, 6), ('ref', 'AC', 1, 1, 0, False), ('ref', 'CO', 1.4, 1.4, 0, False)] \
        + p_active(18.801, 2.5) + lub_pair()
    return cells


def contact_array(left):
    cells = dict(CO=cont(0, 0))
    # elements at x = -8.6, -3.6, 1.4 (columns) and y = -3.6, 1.4 (rows); only (1.4, 1.4) lies on the Activ
    cells[TOP] = [box(NW, 0, 0, 40, 6), ('aref', 'CO', -8.6, -3.6, 0, False, 3, 2, 5.0, 5.0)] + n_active(1, 1) \
        + p_active(left, 2.5) + lub_pair()
    return cells


@control('tie_contact_array_on', 'passed', 'contact of an arrayed cell lands on top-level Activ at one array element; 20.000 um')
def _():
    return contact_array(18.8)


@control('tie_contact_array_limit', 'unverified', 'the same, 20.001 um')
def _():
    return contact_array(18.801)


@control('target_psd_other_cell', 'unverified', 'P+ target exists only flattened: Activ in a child, pSD at top level; no tie in range')
def _():
    cells = dict(AC=[box(ACT, 0, 0, 1, 0.5)])
    cells[TOP] = [box(NW, 0, 0, 60, 6), ('ref', 'AC', 30, 2.5, 0, False), box(PSD, 29.8, 2.3, 31.2, 3.2)] \
        + n_tie(1, 1) + p_active(5, 2.5) + lub_pair()
    return cells


@control('tie_cancelled_by_parent_psd', 'unverified',
         'two instances of a tie cell; top-level pSD covers the only one in range of the target')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 80, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'TIEN', 70, 1, 0, False),
                  box(PSD, 0.8, 0.8, 2.2, 2.2)] + p_active(5, 2.5) + lub_pair()
    return cells


@control('tie_cancelled_by_sibling_block', 'unverified',
         'nSD:block of a sibling cell covers the only n-tie in range of the target')
def _():
    cells = dict(std_cells(), BLK=[box(NSDB, -0.2, -0.2, 1.2, 1.2)])
    cells[TOP] = [box(NW, 0, 0, 80, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'TIEN', 70, 1, 0, False),
                  ('ref', 'BLK', 1, 1, 0, False)] + p_active(5, 2.5) + lub_pair()
    return cells


@control('target_split_over_cells', 'unverified',
         'one P+ Activ made of abutting shapes of two cells: the far half is outside the limit')
def _():
    cells = dict(std_cells(), HALF=p_active(0, 0, 10.0, 0.5))
    cells[TOP] = [box(NW, 0, 0, 60, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'HALF', 1.4, 2.5, 0, False),
                  ('ref', 'HALF', 11.4, 2.5, 0, False), ('ref', 'HALF', 21.4, 2.5, 0, False)] + lub_pair()
    return cells


@control('target_L_bbox', 'unverified',
         'L-shaped P+ Activ from two cells: both arms are in range, the far corner of its bounding box is not')
def _():
    # tie point (1.4, 1.4). Arm X: x 2.4..19.4, y 2.4..2.9; arm Y: x 2.4..2.9, y 2.4..19.4 (each corner 19.5 um).
    # The bounding box of the merged L reaches (19.4, 19.4), 36 um away.
    cells = dict(std_cells(), ARMX=[box(ACT, 0, 0, 17, 0.5), box(PSD, -0.2, -0.2, 17.2, 0.7)],
                 ARMY=[box(ACT, 0, 0, 0.5, 17), box(PSD, -0.2, -0.2, 0.7, 17.2)])
    cells[TOP] = [box(NW, 0, 0, 30, 30), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'ARMX', 2.4, 2.4, 0, False),
                  ('ref', 'ARMY', 2.4, 2.4, 0, False)] + lub_pair()
    return cells


@control('targets_corner_touch', 'unverified',
         'two P+ Activ shapes touch at one corner; each is in range, the far corner of their common bounding box is not',
         old='passed')
def _():
    # tie point (0.4, 0.4). A: x 2..17, y 2..3 (corner 19.2 um); B: x 1..2, y 3..17 (corner 18.2 um); they share (2, 3).
    return {TOP: [box(NW, -1, -1, 30, 30)] + n_tie(0, 0) + [box(ACT, 2, 2, 17, 3), box(PSD, 2, 2, 17, 3),
                                                           box(ACT, 1, 3, 2, 17), box(PSD, 1, 3, 2, 17)] + lub_pair()}


def corner_pair_on_limit(psd, well=()):
    # Tie point (30.4, 1.4). Two 1 nm squares touch at (10.402, 1.4): A x 10.401..10.402, y 1.4..1.401 and
    # B x 10.402..10.403, y 1.399..1.4. Every point of both is within 20.000 um (A's far corner exactly), and
    # their common box, 2 nm wide with an integer centre, is accepted. On its own each square is 1 nm wide:
    # the integer certificate cannot split it and refuses it, 1 nm short.
    return {TOP: [box(NW, 0, 0, 40, 6)] + list(well) + n_tie(30, 1) + [box(ACT, 10.401, 1.4, 10.402, 1.401),
                                                                      box(ACT, 10.402, 1.399, 10.403, 1.4)] + psd + lub_pair()}


@control('corner_pair_on_limit', 'unverified',
         'two 1 nm P+ Activ squares touching at a corner, exactly on the limit: accepted as one box, refused as two')
def _():
    return corner_pair_on_limit([box(PSD, 10.401, 1.4, 10.402, 1.401), box(PSD, 10.402, 1.399, 10.403, 1.4)])


@control('corner_pair_on_limit_one_psd', 'unverified', 'the same under one pSD rectangle')
def _():
    return corner_pair_on_limit([box(PSD, 10.3, 1.3, 10.5, 1.5)])


def one_psd_layout(well, tie, targets, psd, p_tie_at, n_target_at):
    """A layout whose only pSD shape is one rectangle over the P+ targets and the substrate tie, in a
    non-rectangular n-well: there the checker of 2026-11-05 joins target pieces that touch at a corner."""
    return {TOP: well + n_tie(*tie) + targets + [psd, box(ACT, p_tie_at[0], p_tie_at[1], p_tie_at[0] + 1, p_tie_at[1] + 1)]
            + cont(p_tie_at[0] + 0.4, p_tie_at[1] + 0.4) + n_active(n_target_at[0], n_target_at[1], 1.0, 0.5)}


@control('corner_touch_joined_by_old', 'unverified',
         'targets_corner_touch under one pSD rectangle in a notched well: the checker of 2026-11-05 certifies the whole there',
         old_far='passed')
def _():
    return one_psd_layout([box(NW, -1, -1, 30, 30), box(NW, 30, 0, 32, 5)], (0, 0),
                          [box(ACT, 2, 2, 17, 3), box(ACT, 1, 3, 2, 17)], box(PSD, 1, 2, 40, 17), (35, 5), (35, -5))


@control('corner_pair_on_limit_joined_by_old', 'unverified',
         'corner_pair_on_limit under one pSD rectangle in a notched well: accepted as one box by the checker of 2026-11-05',
         old='passed', old_far='unverified')
def _():
    return one_psd_layout([box(NW, 0, 0, 40, 6), box(NW, 0, 6, 5, 8)], (30, 1),
                          [box(ACT, 10.401, 1.4, 10.402, 1.401), box(ACT, 10.402, 1.399, 10.403, 1.4)],
                          box(PSD, 9.9, -5.1, 11.1, 1.5), (10, -5), (14, -5))


# ---------------------------------------------------------------- overlapping instances
@control('overlap_duplicate_instances', 'passed', 'every cell placed twice at the same position')
def _():
    cells = std_cells()
    cells[TOP] = [box(NW, 0, 0, 30, 6)] + 2 * [('ref', 'TIEN', 1, 1, 0, False), ('ref', 'TGTP', 5, 2.5, 0, False),
                                             ('ref', 'TIEP', 100, 100, 0, False), ('ref', 'TGTN', 105, 100, 0, False)]
    return cells


@control('overlap_shifted_ties', 'unverified',
         'two tie cells overlap by half a contact: one merged tie, its point is the lower-left one; 20.001 um')
def _():
    cells = std_cells()
    # contacts at x 1.4..1.56 and 1.48..1.64: merged tie x 1.4..1.64, tie point (1.4, 1.4)
    cells[TOP] = [box(NW, 0, 0, 40, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'TIEN', 1.08, 1, 0, False)] \
        + p_active(18.801, 2.5) + lub_pair()
    return cells


@control('overlap_targets', 'unverified', 'two overlapping target cells: the merged target ends outside the limit')
def _():
    cells = dict(std_cells(), LONG=p_active(0, 0, 10.0, 0.5))
    cells[TOP] = [box(NW, 0, 0, 40, 6), ('ref', 'TIEN', 1, 1, 0, False), ('ref', 'LONG', 5, 2.5, 0, False),
                  ('ref', 'LONG', 9.801, 2.5, 0, False)] + lub_pair()
    return cells


# ---------------------------------------------------------------- refused geometry
def refused(kind, layer_name, where):
    layer = LAYERS[layer_name]
    bad = {'path': ('path', layer, [(50, 40), (53, 40)], 0.2),
           'diagonal': ('poly', layer, [(50, 40), (51, 41), (52, 40)])}[kind]
    cells = std_cells()
    cells[TOP] = lua_pair() + lub_pair()
    if where == 'top':
        cells[TOP].append(bad)
    elif where == 'child':
        cells['BAD'] = [bad]
        cells[TOP].append(('ref', 'BAD', 0, 0, 90, True))
    elif where == 'array':
        cells['BAD'] = [bad]
        cells[TOP].append(('aref', 'BAD', 0, 0, 0, False, 2, 2, 10.0, 10.0))
    elif where == 'grandchild':
        cells['BAD'] = [bad]
        cells['MID'] = [('ref', 'BAD', 0, 0, 180, False)]
        cells[TOP].append(('ref', 'MID', 0, 0, 0, False))
    return cells


for _layer in LAYERS:
    for _kind, _code in (('path', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED'),
                         ('diagonal', 'COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED')):
        for _where in ('top', 'child'):
            control('refuse_%s_%s_%s' % (_kind, _layer, _where), _code, '%s on %s in the %s cell must be refused'
                    % (_kind, _layer, _where))(lambda k=_kind, n=_layer, w=_where: refused(k, n, w))
for _where in ('array', 'grandchild'):
    control('refuse_path_active_%s' % _where, 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED',
            'Activ path in a cell reached through %s' % _where)(lambda w=_where: refused('path', 'active', w))
    control('refuse_diagonal_nwell_%s' % _where, 'COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED',
            'non-rectilinear NWell in a cell reached through %s' % _where)(lambda w=_where: refused('diagonal', 'nwell', w))


@control('path_other_layer', 'passed', 'a Metal1 path is not checker geometry')
def _():
    return {TOP: lua_pair() + lub_pair() + [('path', METAL1, [(50, 40), (53, 40)], 0.2)]}


@control('text_on_checked_layers', 'passed', 'texts on Activ and NWell are not geometry')
def _():
    return {TOP: lua_pair() + lub_pair() + [('text', ACT, 3, 3, 'label'), ('text', NW, 3, 3, 'well')]}


@control('refuse_rotated_45', 'COVERAGE_COMPLEX_TRANSFORM_UNSUPPORTED', 'a cell placed at 45 degrees')
def _():
    cells = std_cells()
    cells[TOP] = lua_pair() + lub_pair() + [('ref', 'TGTP', 5, 4, 45, False)]
    return cells


@control('refuse_magnified', 'COVERAGE_COMPLEX_TRANSFORM_UNSUPPORTED', 'a cell placed with magnification 2')
def _():
    cells = std_cells()
    cells[TOP] = lua_pair() + lub_pair() + [('ref', 'TGTP', 5, 4, 0, False, 2.0)]
    return cells


@control('refuse_two_tops', 'COVERAGE_TOP_CELL_MISMATCH', 'a second top cell')
def _():
    return {TOP: lua_pair() + lub_pair(), 'OTHER': p_active(0, 0)}


@control('property_target', 'unverified', 'a P+ Activ shape that carries a GDS property is still a target')
def _():
    return {TOP: lua_pair() + lub_pair() + [('pbox', ACT, 28, 2.5, 29, 3.0), box(PSD, 27.8, 2.3, 29.2, 3.2)]}


@control('property_path', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED', 'an Activ path under a cell that also has property shapes')
def _():
    return {TOP: lua_pair() + lub_pair() + [('pbox', ACT, 8, 2.5, 9, 3.0), ('path', ACT, [(50, 40), (53, 40)], 0.2)]}


@control('property_target_merge', 'unverified',
         'L-shaped P+ Activ made of a shape with a property and a plain one: still one target, its bounding box fails')
def _():
    # arms as in target_L_bbox: each in range of the tie point (1.4, 1.4), the box corner (19.4, 19.4) is not
    return {TOP: [box(NW, 0, 0, 30, 30)] + n_tie(1, 1) + [('pbox', ACT, 2.4, 2.4, 19.4, 2.9), box(ACT, 2.4, 2.4, 2.9, 19.4),
                                                         box(PSD, 2.2, 2.2, 19.6, 3.1), ('pbox', PSD, 2.2, 2.2, 3.1, 19.6)] + lub_pair()}


@control('property_well_merge', 'passed', 'one n-well domain made of a shape with a property abutting a plain one')
def _():
    return {TOP: [('pbox', NW, 0, 0, 10, 6), box(NW, 10, 0, 20, 6)] + n_tie(1, 1) + p_active(15, 2.5) + lub_pair()}


@control('property_contact_merge', 'unverified',
         'a contact with a property overlapping a plain one: one tie, its point is the lower-left one; 20.001 um')
def _():
    return {TOP: [box(NW, 0, 0, 40, 6)] + n_active(1, 1, 2.0, 1.0) + [('pbox', CONT, 1.4, 1.4, 1.56, 1.56), box(CONT, 1.48, 1.4, 1.64, 1.56)]
            + p_active(18.801, 2.5) + lub_pair()}


@control('property_diagonal', 'COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED', 'a non-rectilinear Activ polygon that carries a property')
def _():
    return {TOP: lua_pair() + lub_pair() + [('ppoly', ACT, [(50, 40), (51, 41), (52, 40)])]}


@control('property_path_itself', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED', 'an NWell path that carries a property')
def _():
    return {TOP: lua_pair() + lub_pair() + [('ppath', NW, [(50, 40), (53, 40)], 0.2)]}


@control('degenerate_diagonal', 'COVERAGE_NONRECTILINEAR_GEOMETRY_UNSUPPORTED', 'a zero-area Activ polygon along a diagonal')
def _():
    return {TOP: lua_pair() + lub_pair() + [('poly', ACT, [(50, 40), (51, 41), (52, 42)])]}


@control('degenerate_flat_polygon', 'passed', 'a zero-area Activ polygon along the x axis adds no geometry')
def _():
    return {TOP: lua_pair() + lub_pair() + [('poly', ACT, [(50, 40), (53, 40), (51, 40)])]}


@control('zero_width_path', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED', 'an NWell path of width zero is still a path')
def _():
    return {TOP: lua_pair() + lub_pair() + [('path', NW, [(50, 40), (53, 40)], 0.0)]}


@control('gds_box_well', 'passed', 'the n-well drawn as a GDS BOX record is still the domain')
def _():
    return {TOP: [('gbox', NW, 0, 0, 30, 6)] + n_tie(1, 1) + p_active(5, 2.5) + lub_pair()}


@control('gds_box_target', 'unverified', 'a P+ Activ drawn as a GDS BOX record is still a target')
def _():
    return {TOP: lua_pair() + lub_pair() + [('gbox', ACT, 28, 2.5, 29, 3.0), box(PSD, 27.8, 2.3, 29.2, 3.2)]}


@control('self_overlapping_target', 'unverified', 'P+ Activ drawn as a polygon that revisits its own edge')
def _():
    # rectilinear ring-like polygon with a zero-width slit, far end outside the limit
    points = [(5, 2.5), (5, 3.0), (28, 3.0), (28, 2.5), (27, 2.5), (27, 2.8), (6, 2.8), (6, 2.5)]
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_tie(1, 1) + [('poly', ACT, points), box(PSD, 4.8, 2.3, 28.2, 3.2)] + lub_pair()}


# ---------------------------------------------------------------- macros
def macro_base(macro_elements, extra_top=(), placements=((200, 50, 0, False), (240, 50, 0, False)), extra_cells=None):
    cells = dict(std_cells(), **(extra_cells or {}))
    cells[MACRO] = [box(NW, 0, 0, 10, 10)] + list(macro_elements)
    cells[TOP] = lua_pair() + lub_pair() + [('ref', MACRO, x, y, a, m) for x, y, a, m in placements] + list(extra_top)
    return cells


@control('macro_untied_inside', 'passed', 'untied P+ Activ inside the excluded macro is not a target', excluded=True)
def _():
    return macro_base(p_active(2, 2))


@control('macro_not_excluded', 'unverified', 'the same layout checked without the exclusion: the macro targets fail')
def _():
    return macro_base(p_active(2, 2))


@control('macro_path_active', 'passed', 'Activ path inside the excluded macro: targets are not read there', excluded=True)
def _():
    return macro_base([('path', ACT, [(1, 1), (3, 1)], 0.2)])


@control('macro_path_nwell', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED', 'NWell path inside the excluded macro: wells are never excluded',
         excluded=True)
def _():
    return macro_base([('path', NW, [(1, 1), (3, 1)], 0.2)])


@control('macro_child_path_nwell', 'COVERAGE_PATH_GEOMETRY_UNSUPPORTED', 'NWell path in a child of the excluded macro', excluded=True)
def _():
    return macro_base([('ref', 'MSUB', 0, 0, 0, False)], extra_cells=dict(MSUB=[('path', NW, [(1, 1), (3, 1)], 0.2)]))


@control('macro_child_untied', 'passed', 'untied P+ Activ in a child of the excluded macro', excluded=True)
def _():
    return macro_base([('ref', 'MSUB', 0, 0, 0, False)], extra_cells=dict(MSUB=p_active(2, 2)))


@control('macro_shared_child_fails_outside', 'unverified',
         'a cell used inside the excluded macro and at top level: its top-level untied P+ Activ is a target', excluded=True)
def _():
    return macro_base([('ref', 'SHARED', 2, 2, 0, False)], extra_top=[box(NW, 0, 20, 30, 26), ('ref', 'SHARED', 5, 22, 0, False)],
                      extra_cells=dict(SHARED=p_active(0, 0)))


@control('macro_shared_child_tie_outside', 'passed',
         'a tie cell used inside the excluded macro and at top level: the top-level one is a tie', excluded=True)
def _():
    cells = macro_base([('ref', 'TIEN', 2, 2, 0, False)])
    cells[TOP] = [box(NW, 0, 0, 30, 6), ('ref', 'TIEN', 1, 1, 0, False)] + p_active(5, 2.5) + lub_pair() \
        + [('ref', MACRO, 200, 50, 0, False), ('ref', MACRO, 240, 50, 0, False)]
    return cells


@control('macro_contact_on_outside_tie', 'unverified',
         'the only contact of a top-level n-tie Activ lies in the excluded macro: it does not count', excluded=True)
def _():
    # macro at (200,50): its contact at local (-3.6,-3.6) lands at (196.4,46.4) on top-level Activ 196..197
    return macro_base(cont(-3.6, -3.6), extra_top=[box(NW, 190, 44, 199, 49)] + n_active(196, 46) + p_active(192, 46))


@control('macro_tie_for_outside_target', 'unverified',
         'a top-level target in a well shared with the macro; the only tie of that well is inside the macro', excluded=True)
def _():
    return macro_base(n_tie(2, 2), extra_top=[box(NW, 190, 52, 200, 56)] + p_active(192, 53))


@control('macro_well_bridges_domains', 'passed',
         'the macro n-well joins two top-level wells: tie in one, target in the other (full well geometry stays)', excluded=True)
def _():
    return macro_base([], extra_top=[box(NW, 192, 52, 200, 56), box(NW, 210, 52, 218, 56)] + n_tie(197, 53) + p_active(212, 54))


@control('macro_well_bridge_missing', 'unverified', 'the same without the macro well overlap: the target well has no tie',
         excluded=True)
def _():
    return macro_base([], extra_top=[box(NW, 192, 52, 199.999, 56), box(NW, 210.001, 52, 218, 56)] + n_tie(197, 53)
                      + p_active(212, 54))


@control('macro_psd_over_outside_tie', 'passed',
         'pSD of the excluded macro over a top-level n-tie: dopant layers of the macro are not read, as before', excluded=True)
def _():
    return macro_base([box(PSD, -4.2, -4.2, -2.8, -2.8)], extra_top=[box(NW, 190, 44, 199, 49)] + n_tie(196, 46) + p_active(192, 46))


@control('macro_rotated_mirrored', 'unverified',
         'macro instances rotated and mirrored: their wells move with them and host an untied top-level target', excluded=True)
def _():
    # second instance R90 at (240,50): macro well (0,0,10,10) -> x 230..240, y 50..60
    return macro_base([], placements=((200, 50, 0, False), (240, 50, 90, False)), extra_top=p_active(232, 52))


@control('macro_count_one', 'COVERAGE_EXCLUSION_COUNT', 'only one macro instance where two are declared', excluded=True)
def _():
    return macro_base([], placements=((200, 50, 0, False),))


@control('macro_count_three', 'COVERAGE_EXCLUSION_COUNT', 'three macro instances where two are declared', excluded=True)
def _():
    return macro_base([], placements=((200, 50, 0, False), (240, 50, 0, False), (280, 50, 0, False)))


@control('macro_missing', 'COVERAGE_EXCLUSION_MISSING', 'the declared macro cell does not exist', excluded=True)
def _():
    return {TOP: lua_pair() + lub_pair()}


@control('macro_nested', 'passed', 'both macro instances sit inside a wrapper cell', excluded=True)
def _():
    cells = macro_base(p_active(2, 2), placements=())
    cells['WRAP'] = [('ref', MACRO, 0, 0, 0, False), ('ref', MACRO, 40, 0, 180, True)]
    cells[TOP].append(('ref', 'WRAP', 200, 50, 0, False))
    return cells


# ---------------------------------------------------------------- empty layers and vacuous layouts
@control('empty_no_contacts', 'unverified', 'no contact shape at all')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_active(1, 1) + p_active(5, 2.5) + p_active(100, 100, 1.0, 1.0)
            + n_active(105, 100, 1.0, 0.5)}


@control('empty_no_nwell', 'unverified', 'no n-well: LU.a has nothing to certify and must not pass vacuously')
def _():
    return {TOP: lub_pair()}


@control('empty_no_activ', 'unverified', 'wells and contacts but no Activ')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + cont(1.4, 1.4) + cont(100.4, 100.4)}


@control('empty_no_psd', 'unverified', 'no pSD: no P+ target and no substrate tie')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)] + n_tie(1, 1) + n_tie(100, 100) + n_active(105, 100, 1.0, 0.5)}


@control('empty_top', 'unverified', 'an empty top cell')
def _():
    return {TOP: []}


@control('empty_only_wells', 'unverified', 'n-well only')
def _():
    return {TOP: [box(NW, 0, 0, 30, 6)]}


@control('empty_no_substrate_target', 'unverified', 'LU.a pair only: LU.b has no target and must not pass vacuously')
def _():
    return {TOP: lua_pair()}


# ---------------------------------------------------------------- in-well distance (LU.a only)
@control('inwell_u_far', 'unverified', 'U-shaped n-well: target 10 um from the tie in a straight line, 76 um inside the well',
         old='passed')
def _():
    return {TOP: [box(NW, 0, 0, 4, 40), box(NW, 10, 0, 14, 40), box(NW, 0, 0, 14, 4)] + n_tie(1.5, 37)
            + p_active(11.5, 37) + lub_pair()}


@control('inwell_u_near', 'passed', 'U-shaped n-well: in-well path about 15 um around the bottom')
def _():
    return {TOP: [box(NW, 0, 0, 4, 8), box(NW, 6, 0, 10, 8), box(NW, 0, 0, 10, 3)] + n_tie(1.5, 6)
            + p_active(7.5, 6) + lub_pair()}


@control('inwell_notch', 'passed', 'strip with a notch between tie and target: path around the notch about 8 um')
def _():
    return {TOP: [box(NW, 0, 0, 12, 4), box(NW, 12, 0, 15, 2), box(NW, 15, 0, 30, 4)] + n_tie(9, 2.5)
            + p_active(16, 3) + lub_pair()}


@control('inwell_comb', 'unverified', 'two strips joined by a spine far away: the tied strip is 6.72 um from the target strip',
         old='passed')
def _():
    return {TOP: [box(NW, 0, 0, 60, 4), box(NW, 0, 10.72, 60, 14.72), box(NW, -1, 0, 0, 14.72)] + n_tie(30, 1.5)
            + p_active(35, 1.5) + p_active(30, 12) + lub_pair()}


# ---------------------------------------------------------------- random hierarchical layouts
def snap(rng, low, high, step=0.5):
    value = round(rng.uniform(low, high) / step) * step
    if rng.random() < 0.08:
        value += rng.choice((-0.001, 0.001))
    return round(value, 3)


def rect(rng, layer, x, y, wmax, hmax, wmin=0.5, hmin=0.5):
    w, h = snap(rng, wmin, wmax), snap(rng, hmin, hmax)
    return ('box', layer, x, y, round(x + max(w, 0.001), 3), round(y + max(h, 0.001), 3))


def leaf(rng, size):
    """Random shapes inside about size x size um: wells, diffusion, dopants, contacts."""
    out = []
    for _ in range(rng.choice((0, 1, 1, 2, 3))):
        out.append(rect(rng, NW, snap(rng, 0, size * 0.6), snap(rng, 0, size * 0.6), size * 0.8, size * 0.5, 1.0, 1.0))
    actives = []
    for _ in range(rng.choice((1, 2, 3, 4, 6))):
        shape = rect(rng, ACT, snap(rng, 0, size), snap(rng, 0, size), 4.0, 2.0)
        x0, y0, x1, y1 = shape[2:]
        mx, my = round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)
        if rng.random() < 0.15 and x0 < mx < x1 and y0 < my < y1:  # an L drawn as a polygon
            shape = ('poly', ACT, [(x0, y0), (x0, y1), (mx, y1), (mx, my), (x1, my), (x1, y0)])
        actives.append((x0, y0, x1, y1))
        out.append(shape)
    for x0, y0, x1, y1 in actives:
        roll = rng.random()
        if roll < 0.45:  # pSD over all or part of it
            cut = rng.choice((0.0, 0.0, (x1 - x0) / 2))
            out.append(('box', PSD, round(x0 - 0.2 + cut, 3), round(y0 - 0.2, 3), round(x1 + 0.2, 3), round(y1 + 0.2, 3)))
        elif roll < 0.52:
            out.append(('box', NSDB, round(x0 - 0.1, 3), round(y0 - 0.1, 3), round((x0 + x1) / 2, 3), round(y1 + 0.1, 3)))
        elif roll < 0.56:
            out.append(('box', NSD, x0, y0, x1, y1))
        if rng.random() < 0.6:  # a contact on, across or beside it
            cx = round(rng.choice((x0 + 0.1, x0 - 0.08, x1 - 0.08, x1, (x0 + x1) / 2)), 3)
            cy = round(rng.choice((y0 + 0.1, y0 - 0.08, y1 - 0.08, y1, (y0 + y1) / 2)), 3)
            side = rng.choice((0.16, 0.16, 0.3))
            out.append(('box', CONT, cx, cy, round(cx + side, 3), round(cy + side, 3)))
            if rng.random() < 0.15:  # a second contact overlapping or touching the first
                dx = rng.choice((0.08, 0.16, -0.16))
                out.append(('box', CONT, round(cx + dx, 3), round(cy + rng.choice((0, 0.16)), 3),
                            round(cx + dx + side, 3), round(cy + rng.choice((0, 0.16)) + side, 3)))
    if rng.random() < 0.08:
        out.append(rect(rng, PWB, snap(rng, 0, size), snap(rng, 0, size), 5.0, 5.0))
    if rng.random() < 0.05:
        out.append(rect(rng, PWD, snap(rng, 0, size), snap(rng, 0, size), 5.0, 5.0))
    if rng.random() < 0.05:
        out.append(('text', rng.choice((ACT, NW, CONT)), 1.0, 1.0, 'label'))
    return out


def instance(rng, child, span):
    x, y = snap(rng, 0, span), snap(rng, 0, span)
    angle, mirror = rng.choice((0, 90, 180, 270)), rng.random() < 0.4
    if rng.random() < 0.25:
        pitch = rng.choice((0.5, 1.0, 2.0, 4.0, 8.0, 12.0))  # small pitches overlap the elements
        return ('aref', child, x, y, angle, mirror, rng.randint(1, 4), rng.randint(1, 3), pitch,
                rng.choice((0.5, 2.0, 6.0, 12.0)))
    return ('ref', child, x, y, angle, mirror)


def strip_wells(elements):
    return [element for element in elements if element[1] != NW]


def random_layout(seed):
    """(cells, uses the macro) of one random hierarchical layout; the same seed gives the same bytes."""
    rng = random.Random(seed)
    span = rng.choice((20.0, 40.0, 70.0))
    single_well, carpet, bad = rng.random() < 0.25, rng.random() < 0.5, rng.random() < 0.06
    cells, leaves = {}, []
    for number in range(rng.randint(1, 4)):
        name = 'L%d' % number
        cells[name] = leaf(rng, rng.choice((6.0, 12.0, 25.0)))
        leaves.append(name)
    mids = []
    for number in range(rng.randint(0, 2)):
        name = 'M%d' % number
        cells[name] = [instance(rng, rng.choice(leaves), 15.0) for _ in range(rng.randint(1, 3))] \
            + (leaf(rng, 10.0) if rng.random() < 0.3 else [])
        mids.append(name)
    excluded = False
    top = leaf(rng, span) + leaf(rng, span)
    # one or two large wells so that many targets and ties share a domain
    for _ in range(rng.choice((0, 1, 1, 2))):
        top.append(rect(rng, NW, snap(rng, 0, span * 0.5), snap(rng, 0, span * 0.5), span, span * 0.6, 4.0, 2.0))
    for _ in range(rng.randint(1, 7)):
        top.append(instance(rng, rng.choice(leaves + mids), span))
    if rng.random() < 0.3:  # repeated and abutting placements
        child = rng.choice(leaves)
        x, y = snap(rng, 0, span), snap(rng, 0, span)
        top += [('ref', child, x, y, 0, False), ('ref', child, x, y, 0, False),
                ('ref', child, round(x + rng.choice((6.0, 12.0, 0.5)), 3), y, 0, rng.random() < 0.5)]
    if rng.random() < 0.4:
        excluded = True
        cells[MACRO] = leaf(rng, 15.0) + [instance(rng, rng.choice(leaves), 8.0) for _ in range(rng.randint(0, 2))]
        pair = [('ref', MACRO, snap(rng, 0, span), snap(rng, 0, span), rng.choice((0, 90, 180, 270)), rng.random() < 0.3)
                for _ in range(2)]  # the declared count is two
        if rng.random() < 0.3:  # both under one wrapper cell
            cells['WRAP'] = pair
            top.append(('ref', 'WRAP', snap(rng, 0, 10.0), snap(rng, 0, 10.0), rng.choice((0, 90, 180, 270)), rng.random() < 0.3))
        else:
            top += pair
    if carpet:  # ties on a pitch under the limit: most targets are then in range, so verdicts can pass
        cells['TIEN'] = [('box', ACT, 0, 0, 0.6, 0.6), ('box', CONT, 0.2, 0.2, 0.36, 0.36)]
        cells['TIEP'] = cells['TIEN'] + [('box', PSD, -0.1, -0.1, 0.7, 0.7)]
        pitch = rng.choice((6.0, 9.0, 13.0))
        count = int(span * 1.6 / pitch) + 2
        top += [('aref', 'TIEN', -span * 0.3, -span * 0.3, 0, False, count, count, pitch, pitch),
                ('aref', 'TIEP', -span * 0.3 + pitch / 2, -span * 0.3 + 1.5, 0, False, count, count, pitch, pitch)]
    cells[TOP] = top
    if single_well:  # one rectangular n-well in the whole layout: one domain per rule in most cases
        cells = {cell: strip_wells(elements) for cell, elements in cells.items()}
        cells[TOP].append(('box', NW, -span * 0.1, -span * 0.1, span * 0.7, span * 0.6))
    if bad:  # a shape that must be refused (or ignored, inside the macro on a target layer), anywhere
        layer = rng.choice((ACT, PSD, NSD, NSDB, NW, PWB, PWD, CONT, (8, 0)))
        shape = rng.choice((('path', layer, [(1, 1), (3, 1)], 0.2), ('poly', layer, [(1, 1), (2, 2), (3, 1)]),
                            ('ppath', layer, [(1, 1), (3, 1)], 0.2), ('ppoly', layer, [(1, 1), (2, 2), (3, 1)])))
        cells[rng.choice(sorted(cells))].append(shape)
    used, stack = set(), [TOP]
    while stack:
        cell = stack.pop()
        if cell not in used:
            used.add(cell)
            stack += [element[1] for element in cells[cell] if element[0] in ('ref', 'aref')]
    return {cell: elements for cell, elements in cells.items() if cell in used}, excluded
