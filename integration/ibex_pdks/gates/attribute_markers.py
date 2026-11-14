# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Separate the markers a hard macro produces on its own from every other marker.

KLayout batch script (`klayout -b -r attribute_markers.py -rd config=<json>`); KLayout is used
only as a geometry library through its public Python API.

Rule. The same deck with the same options is run once on the macro's GDS alone (the baseline).
A marker of the checked layout is attributed to a macro if and only if, for the same rule name,
its geometry lies inside the baseline markers of that rule, placed at one of the macro's instances
with that instance's transformation. Every other marker gates. A marker that exists only because
top-level geometry is present cannot be in a baseline that was computed without any top-level
geometry, so it is never attributed, wherever it lies. Markers without geometry always gate.

The comparison is valid only if the macro in the layout is the macro: the flat geometry of every
macro master in the layout must equal that of the macro's own GDS on every layer, otherwise the
run fails.

Marker sources are KLayout report databases (hierarchical: an item belongs to the cell it was
reported in and counts once per flat occurrence of that cell; an item of a cell variant is placed
by the report's own references) or box lists in top-cell coordinates.
Zero-area geometry (edges, edge pairs) is widened by one database unit on both sides, the same
way in the baseline and in the layout. Memory: the layout is read once; flat occurrences are
capped by `limits.occurrences`.
"""
import json

import pya


def fail(message):
    raise RuntimeError(message)


def flat_counts(layout, top):
    """Number of flat occurrences of every cell below `top`."""
    counts = {top.cell_index(): 1}
    order = [layout.cell(i) for i in top.called_cells()]
    # callers before callees: sort by hierarchy depth from the top
    depth = {top.cell_index(): 0}
    pending = [top]
    while pending:
        cell = pending.pop()
        for child in cell.each_child_cell():
            if depth.get(child, -1) < depth[cell.cell_index()] + 1:
                depth[child] = depth[cell.cell_index()] + 1
                pending.append(layout.cell(child))
    for cell in sorted(order, key=lambda c: depth[c.cell_index()]):
        total = 0
        for parent in cell.each_parent_inst():
            parent_count = counts.get(parent.parent_cell_index())
            if parent_count:
                total += parent_count * parent.child_inst().size()
        counts[cell.cell_index()] = total
    return counts


def to_polygons(value, dbu):
    """Integer polygons of one report value; None when the value has no geometry."""
    if value.is_polygon():
        return [value.polygon().to_itype(dbu)]
    if value.is_box():
        box = value.box().to_itype(dbu)
        if box.width() == 0 or box.height() == 0:
            box = box.enlarged(1, 1)
        return [pya.Polygon(box)]
    if value.is_edge_pair():
        return [value.edge_pair().to_itype(dbu).polygon(1)]
    if value.is_edge():
        edge = value.edge().to_itype(dbu)
        return [pya.Polygon(pya.Box(edge.p1, edge.p2).enlarged(1, 1))]
    if value.is_path():
        return [value.path().to_itype(dbu).polygon()]
    return None


class Markers:
    """Markers of one run inserted into a layout's own cells, one scratch layer per rule."""

    def __init__(self, layout, top, limit):
        self.layout, self.top, self.limit = layout, top, limit
        self.layers, self.items, self.flat, self.no_geometry, self.unknown_cells = {}, {}, {}, {}, {}
        self.counts = flat_counts(layout, top)

    def layer(self, rule):
        if rule not in self.layers:
            self.layers[rule] = self.layout.insert_layer(pya.LayerInfo())
            self.items[rule] = 0
            self.flat[rule] = 0
        return self.layers[rule]

    def add(self, rule, cell, polygons):
        layer = self.layer(rule)
        self.items[rule] += 1
        if polygons is None:
            self.no_geometry[rule] = self.no_geometry.get(rule, 0) + 1
            return
        if cell is None:
            self.unknown_cells[rule] = self.unknown_cells.get(rule, 0) + 1
            return
        occurrences = self.counts.get(cell.cell_index(), 0)
        if occurrences == 0:
            self.unknown_cells[rule] = self.unknown_cells.get(rule, 0) + 1
            return
        self.flat[rule] += occurrences * len(polygons)
        if sum(self.flat.values()) > self.limit:
            fail("ATTRIBUTION_OCCURRENCE_BUDGET")
        for polygon in polygons:
            cell.shapes(layer).insert(polygon)

    def variant_placements(self, database, cell, depth=0):
        """Flat placements of a report cell that is one variant of a layout cell, from the report's own references."""
        if depth > 32:
            fail("REPORT_REFERENCE_DEPTH")
        references = list(cell.each_reference())
        if not references:
            return [pya.ICplxTrans()] if cell.name() == self.top.name and not cell.variant() else []
        to_dbu = pya.CplxTrans(self.layout.dbu)
        result = []
        for reference in references:
            local = to_dbu.inverted() * reference.trans() * to_dbu
            parent = database.cell_by_id(reference.parent_cell_id())
            result += [above * local for above in self.variant_placements(database, parent, depth + 1)]
            if len(result) > self.limit:
                fail("ATTRIBUTION_OCCURRENCE_BUDGET")
        return result

    def load(self, source):
        if source["kind"] == "lyrdb":
            database = pya.ReportDatabase("markers")
            database.load(source["path"])
            dbu = self.layout.dbu
            self.variant_items = 0
            for item in database.each_item():
                rule = database.category_by_id(item.category_id()).path().replace("'", "")
                report_cell = database.cell_by_id(item.cell_id())
                polygons = []
                for value in item.each_value():
                    converted = to_polygons(value, dbu)
                    if converted:
                        polygons += converted
                if report_cell.variant() and polygons:
                    # The deck gave this marker to some instances of a cell only: place it where
                    # the report says, in top-cell coordinates.
                    self.variant_items += 1
                    placements = self.variant_placements(database, report_cell)
                    flat = [polygon.transformed(trans) for trans in placements for polygon in polygons]
                    self.layer(rule)
                    if flat:
                        self.add(rule, self.top, flat)
                    else:
                        self.add(rule, None, polygons)
                    continue
                cell = self.layout.cell(report_cell.layout_name() or report_cell.name())
                self.add(rule, cell, polygons or None)
            self.declared = sorted(c.path().replace("'", "") for c in database.each_category() if c.num_items() == 0)
        elif source["kind"] == "boxes":
            data = json.load(open(source["path"]))
            scale = data["unit_um"] / self.layout.dbu
            for rule, boxes in data["rules"].items():
                for box in boxes:
                    values = [v * scale for v in box]
                    if any(abs(v - round(v)) > 1e-6 for v in values):
                        fail("BOX_OFF_DATABASE_GRID: " + rule)
                    x0, y0, x1, y1 = (int(round(v)) for v in values)
                    shape = pya.Box(x0, y0, x1, y1)
                    if shape.width() == 0 or shape.height() == 0:
                        shape = shape.enlarged(1, 1)
                    self.add(rule, self.top, [pya.Polygon(shape)])
            self.declared = []
        else:
            fail("MARKER_SOURCE_KIND")

    def region(self, rule):
        region = pya.Region(self.top.begin_shapes_rec(self.layers[rule]))
        region.merged_semantics = False
        return region


def instances_of(layout, top, master):
    target = layout.cell(master)
    if target is None:
        return None
    result = []
    iterator = top.begin_instances_rec()
    iterator.targets = [target.cell_index()]
    while not iterator.at_end():
        trans = iterator.trans() * iterator.inst_trans()
        result.append((trans, str(trans)))
        iterator.next()
    return result


def geometry_difference(layout, cell, reference_layout, reference_cell):
    """Layers on which the flat geometry of a macro in the layout differs from the macro's own GDS."""
    pairs = {}
    for source, index in ((layout, 0), (reference_layout, 1)):
        for layer in source.layer_indexes():
            info = source.get_info(layer)
            if info.layer < 0:
                continue  # scratch layers of this script
            pairs.setdefault((info.layer, info.datatype), [None, None])[index] = layer
    differing = []
    for key, (left, right) in sorted(pairs.items()):
        a = pya.Region(cell.begin_shapes_rec(left)) if left is not None else pya.Region()
        b = pya.Region(reference_cell.begin_shapes_rec(right)) if right is not None else pya.Region()
        if not (a ^ b).is_empty():
            differing.append("%d/%d" % key)
    return differing


def main():
    config = json.load(open(config_path))  # noqa: F821  (-rd config_path=...)
    limit = int(config["limits"]["occurrences"])
    layout = pya.Layout()
    layout.read(config["layout"])
    top = layout.cell(config["top"])
    if top is None:
        fail("TOP_CELL_MISSING: " + config["top"])
    full = Markers(layout, top, limit)
    full.load(config["source"])
    instances, baseline_regions, baseline_facts = {}, {}, {}
    for master, spec in sorted(config["macros"].items()):
        found = instances_of(layout, top, master)
        if found is None or len(found) != spec["instances"]:
            fail("MACRO_INSTANCE_COUNT: %s expected %d found %s"
                 % (master, spec["instances"], "none" if found is None else len(found)))
        instances[master] = found
        macro_layout = pya.Layout()
        macro_layout.read(spec["gds"])
        if abs(macro_layout.dbu - layout.dbu) > 1e-12:
            fail("MACRO_DBU_DIFFERS: " + master)
        macro_top = macro_layout.cell(master)
        if macro_top is None:
            fail("MACRO_TOP_MISSING: " + master)
        differing = geometry_difference(layout, layout.cell(master), macro_layout, macro_top)
        if differing:
            fail("MACRO_GEOMETRY_DIFFERS_FROM_ITS_GDS: %s layers %s" % (master, differing[:8]))
        base = Markers(macro_layout, macro_top, limit)
        base.load(spec["baseline"])
        if base.no_geometry or base.unknown_cells:
            fail("BASELINE_MARKER_WITHOUT_PLACE: " + master)
        baseline_regions[master] = {rule: base.region(rule) for rule in base.layers}
        baseline_facts[master] = dict(items=dict(sorted(base.items.items())), occurrences=dict(sorted(base.flat.items())),
                                      instances=[text for _, text in found])
    rules = {}
    for rule in sorted(set(full.layers) | {r for regions in baseline_regions.values() for r in regions}):
        placed = pya.Region()
        expected = 0
        for master, regions in baseline_regions.items():
            if rule not in regions:
                continue
            for trans, _ in instances[master]:
                placed += regions[rule].transformed(trans)
            expected += regions[rule].count() * len(instances[master])
        record = dict(items=full.items.get(rule, 0), occurrences=0, macro_interior=0, gating=0,
                      without_geometry=full.no_geometry.get(rule, 0), unknown_cell=full.unknown_cells.get(rule, 0),
                      baseline_occurrences=expected, baseline_reproduced=0, samples=[])
        if rule in full.layers:
            region = full.region(rule)
            record["occurrences"] = region.count()
            if expected:
                cover = placed.merged()
                outside = region.not_inside(cover)
                record["macro_interior"] = record["occurrences"] - outside.count()
                merged_full = region.merged()
                reproduced = 0
                for master, regions in baseline_regions.items():
                    if rule in regions:
                        for trans, _ in instances[master]:
                            piece = regions[rule].transformed(trans)
                            piece.merged_semantics = False
                            reproduced += piece.inside(merged_full).count()
                record["baseline_reproduced"] = reproduced
            else:
                outside = region
            record["gating"] = outside.count() + record["without_geometry"] + record["unknown_cell"]
            for polygon in outside.each():
                if len(record["samples"]) >= 5:
                    break
                box = polygon.bbox()
                record["samples"].append([round(v * layout.dbu, 4) for v in (box.left, box.bottom, box.right, box.top)])
        else:
            record["gating"] = record["without_geometry"] + record["unknown_cell"]
        rules[rule] = record
    totals = {key: sum(r[key] for r in rules.values()) for key in
              ("items", "occurrences", "macro_interior", "gating", "without_geometry", "unknown_cell",
               "baseline_occurrences", "baseline_reproduced")}
    result = dict(schema="rusteda.pdk-gate.marker-attribution.v1", status="completed", top=config["top"],
                  dbu_um=layout.dbu, rule=__doc__.split("Rule. ", 1)[1].split("\n\n", 1)[0].replace("\n", " "),
                  macros=baseline_facts, macro_geometry="identical to the macro GDS on every layer",
                  instance_specific_items=getattr(full, "variant_items", 0),
                  rules=rules, totals=totals, rules_without_markers=len(full.declared),
                  gating_rules={k: v["gating"] for k, v in rules.items() if v["gating"]},
                  macro_interior_rules={k: v["macro_interior"] for k, v in rules.items() if v["macro_interior"]})
    with open(config["output"], "w") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")


try:
    main()
except Exception as error:  # the caller reads the status file, not the exit code of the embedding tool
    import traceback
    traceback.print_exc()
    try:
        target = json.load(open(config_path))["output"]  # noqa: F821
        with open(target, "w") as stream:
            json.dump(dict(schema="rusteda.pdk-gate.marker-attribution.v1", status="failed", error=str(error)), stream)
    finally:
        raise SystemExit(1)
