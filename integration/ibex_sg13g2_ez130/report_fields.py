# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Bounded top-level JSON field reader for large external/native reports.

Unselected geometry/check arrays are scanned, never materialized. Selected values
use the ordinary JSON decoder and a separate byte cap. This is a metadata reader,
not validation of the semantics of skipped fields; raw report identities and the
independent physical signoff gates remain mandatory.
"""
from __future__ import annotations

import json
from pathlib import Path


def prefix_fields(path, selected, max_bytes=65536):
    """Read only canonical leading metadata, rejecting a large early value."""
    with Path(path).open('rb') as stream:
        text = stream.read(max_bytes).decode('utf-8', errors='strict')
    decoder, cursor, result, keys = json.JSONDecoder(), 0, {}, set()
    def whitespace():
        nonlocal cursor
        while cursor < len(text) and text[cursor] in ' \t\r\n':
            cursor += 1
    whitespace()
    if cursor >= len(text) or text[cursor] != '{':
        raise ValueError('HEADER_OBJECT_REQUIRED')
    cursor += 1
    for index in range(128):
        whitespace()
        if index:
            if cursor >= len(text) or text[cursor] != ',':
                raise ValueError('HEADER_FIELD_BUDGET_OR_SEPARATOR')
            cursor += 1
            whitespace()
        key, cursor = decoder.raw_decode(text, cursor)
        if not isinstance(key, str) or key in keys:
            raise ValueError('HEADER_DUPLICATE_OR_INVALID_KEY')
        keys.add(key)
        whitespace()
        if cursor >= len(text) or text[cursor] != ':':
            raise ValueError('HEADER_KEY_SEPARATOR')
        cursor += 1
        whitespace()
        try:
            value, cursor = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError as error:
            raise ValueError('HEADER_FIELD_BYTE_BUDGET_OR_FORMAT') from error
        if key in selected:
            result[key] = value
        if set(selected) <= result.keys():
            return result
    raise ValueError('HEADER_FIELD_COUNT_BUDGET')


def fields(path, selected, max_bytes=256 << 20, max_field_bytes=16 << 20, max_selected_bytes=32 << 20):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('REPORT_BYTE_BUDGET')
    result, total, captured = {}, 0, 0
    with path.open('rb') as stream:
        chunk, offset = b'', 0
        def byte():
            nonlocal chunk, offset, total
            if offset == len(chunk):
                chunk, offset = stream.read(65536), 0
                if not chunk:
                    return None
            value = chunk[offset]
            offset += 1
            total += 1
            if total > max_bytes:
                raise ValueError('REPORT_BYTE_BUDGET')
            return value
        def nonspace():
            value = byte()
            while value in (9, 10, 13, 32):
                value = byte()
            return value
        if nonspace() != 123:
            raise ValueError('REPORT_OBJECT_REQUIRED')
        first, keys = True, set()
        while True:
            value = nonspace()
            if value == 125:
                break
            if not first:
                if value != 44:
                    raise ValueError('REPORT_TOPLEVEL_SEPARATOR')
                value = nonspace()
            first = False
            if value != 34:
                raise ValueError('REPORT_TOPLEVEL_KEY')
            raw = bytearray([34])
            escaped = False
            while True:
                value = byte()
                if value is None or value < 32 or len(raw) >= 8192:
                    raise ValueError('REPORT_KEY_BUDGET_OR_FORMAT')
                raw.append(value)
                if not escaped and value == 34:
                    break
                escaped = not escaped and value == 92
            key = json.loads(raw)
            if key in keys or len(keys) >= 128:
                raise ValueError('REPORT_DUPLICATE_KEY_OR_BUDGET')
            keys.add(key)
            if nonspace() != 58:
                raise ValueError('REPORT_KEY_VALUE_SEPARATOR')
            value = nonspace()
            if value is None or value in (44, 125):
                raise ValueError('REPORT_MISSING_VALUE')
            keep = key in selected
            raw = bytearray()
            stack, quoted, escaped = [], False, False
            while True:
                if keep:
                    raw.append(value)
                    if len(raw) > max_field_bytes:
                        raise ValueError('REPORT_FIELD_BYTE_BUDGET')
                if quoted:
                    if value < 32:
                        raise ValueError('REPORT_STRING_CONTROL')
                    if not escaped and value == 34:
                        quoted = False
                    escaped = not escaped and value == 92
                elif value == 34:
                    quoted = True
                elif value in (91, 123):
                    stack.append(value)
                    if len(stack) > 64:
                        raise ValueError('REPORT_DEPTH_BUDGET')
                elif value in (93, 125):
                    if not stack or stack.pop() != (91 if value == 93 else 123):
                        raise ValueError('REPORT_UNBALANCED_VALUE')
                following = byte()
                if following is None:
                    raise ValueError('REPORT_TRUNCATED')
                if not quoted and not stack and following in (44, 125):
                    # Put back one structural delimiter in the small read buffer.
                    offset -= 1
                    total -= 1
                    break
                value = following
            if keep:
                captured += len(raw)
                if captured > max_selected_bytes:
                    raise ValueError('REPORT_SELECTED_BYTE_BUDGET')
                result[key] = json.loads(raw)
        if nonspace() is not None:
            raise ValueError('REPORT_TRAILING_DATA')
    if not set(selected) <= keys:
        raise ValueError('REPORT_REQUIRED_FIELD_MISSING')
    return result
