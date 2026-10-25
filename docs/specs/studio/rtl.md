# RTL cross-probing

A revision made by a synthesis carries the RTL it started from as the directory table `rtl`:
the source files and `rtl.json` (`{top, defines, params, files, predefined_macros}`, RCLI-REQ-0014).
The netlist is flat and most of its names are generated, but the names the synthesis keeps carry
the RTL path: state nets (`u_cnt.cnt_q[3]`, the instance path joined with `.`, then the variable
and its indices) and ports. Cross-probing maps a netlist object to source lines by matching
these parts against a light index of the snapshot. It is name matching, not elaboration, so
every result is approximate.

**STUDIO-REQ-0120** The RTL index reads the files of the snapshot through the checked accessors
of the directory table (WS-REQ-0026): `rtl.json` for the top module, and every other file up to
`rtl_max_file_bytes` each and `rtl_max_total_bytes` in all. A file over the budget, unreadable or
not UTF-8 is listed without text and reported as `U0031`.

**STUDIO-REQ-0121** The index lexes each file into identifiers and punctuation, skipping line and
block comments, strings and numbers; an escaped identifier loses its backslash. It records the
module definitions (`module` or `macromodule` and a name, up to `endmodule`), the instantiations
of modules defined in the snapshot (`type [#(...)] name [ranges] (`, in the body of a module),
and the generate block labels (`begin : label`), each with its 1-based line.

**STUDIO-REQ-0122** A cross-probe returns at most `rtl_max_hits` source lines, the best first
(by score, then file, then line), each with its file, line, kind, module, score and the reason it
matched; a line matched by several candidates keeps its best score. It says whether hits were
left out.

**STUDIO-REQ-0123** A matched line is a declaration when it starts with a declaration keyword
(`input`, `output`, `inout`, `wire`, `reg`, `logic`, `bit`, `var`, the integer types,
`parameter`, `localparam`, `genvar`, the net types, `signed`, `unsigned`, `typedef`, `enum`,
`struct`), an assignment when the identifier, after an optional select, is followed by `=` (not
`==`) or `<=`, and a reference otherwise; the lines of the instantiations and generate labels
along the path are hits of their own kind. Kinds score, from high to low: declaration,
assignment, instantiation, label, reference.

**STUDIO-REQ-0124** The candidates of a probe are names with a role and a weight: for an
instance, its own name (100), then the names of the nets on its output pins (80), then those on
its input pins (40), at most 16; for a port, its name. The RTL parts of a name are its levels in
the flattened mode ([hierarchy.md](hierarchy.md)) with bit and array indices removed. The scope
parts are followed from the snapshot's top module (else the module named like the netlist's
module): through an instantiation of that name into the instantiated module, or through a
generate label of that name within the same module; a part that resolves neither way stops the
walk, and the matches of the last part then score half the candidate's weight less. The last part
is looked up in the module reached, and also without a trailing `_reg`.

**STUDIO-REQ-0125** Every cross-probe is flagged approximate and reported as the note `U0070`; a
probe without any line is the note `U0071`.

**STUDIO-REQ-0126** The source pane shows the file of the best hit, scrolled to its line, with
every hit of the probe in that file highlighted; choosing another hit shows its file and line.
Focusing a path probes its endpoint's instance; a click on a point of the path detail probes that
point's instance without changing the focus.
