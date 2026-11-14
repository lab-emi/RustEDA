#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Drop the inout ports of `(* blackbox *)` module declarations (the supply ports of a hard macro).

RustSys accepts only scalar or one-dimensional input and output ports on an opaque macro; the physical
flow takes the macro's supply pins from its LEF. Nothing else of the design text is changed.
"""
import re
import sys

src, dst = sys.argv[1:]
text = open(src).read()


def fix(match):
    block = match.group(0)
    names = re.findall(r"^\s*inout\s+(\w+)\s*;\s*\n", block, re.M)
    if not names:
        return block
    block = re.sub(r"^\s*inout\s+\w+\s*;\s*\n", "", block, flags=re.M)
    head_end = block.index(");")
    head, rest = block[:head_end], block[head_end:]
    for name in names:
        head = re.sub(r",\s*\n\s*%s\b" % re.escape(name), "", head)
    return head + rest


out, count = re.subn(r"\(\* blackbox \*\) module .*?endmodule", fix, text, flags=re.S)
if not count:
    sys.exit("no black-box module in %s" % src)
open(dst, "w").write(out)
