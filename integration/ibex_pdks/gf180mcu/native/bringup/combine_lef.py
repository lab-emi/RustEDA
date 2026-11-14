#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Combined cell LEF: the library LEF without its final END LIBRARY, the MACRO blocks of the macro LEFs,
END LIBRARY (the rule of the native profile schema for `lef.combined: null`)."""
import re
import sys

lib, *macros, out = sys.argv[1:]
text = open(lib).read()
end = text.rindex("END LIBRARY")
parts = [text[:end].rstrip("\n") + "\n"]
for path in macros:
    body = open(path).read()
    blocks = re.findall(r"^MACRO (\S+)\n.*?^END \1[ \t]*\n", body, re.S | re.M)
    found = [m.group(0) for m in re.finditer(r"^MACRO (\S+)\n.*?^END \1[ \t]*\n", body, re.S | re.M)]
    if not found or len(found) != len(blocks):
        sys.exit("no MACRO block in %s" % path)
    parts.extend(found)
parts.append("END LIBRARY\n")
open(out, "w").write("".join(parts))
