// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 RustEDA contributors

//! Ordered JSON values with the exact text form of the reference producer.
//!
//! Rendered templates, command records and receipts are compared byte for byte
//! with files written by `json.dump(value, indent=2)`, so this module keeps the
//! member order of every object, distinguishes integers from floats as that
//! reader does (`1` is an integer, `1.0` and `1e3` are floats), prints floats
//! in their shortest round-trip form with the same layout rules, and escapes
//! every character outside printable ASCII. A document is at most 512 levels
//! deep. `NaN` and the infinities are rejected on input and on output.

use crate::error::{Error, Result};
use std::fmt::Write as _;

const MAX_DEPTH: usize = 512;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    /// Canonical decimal digits with an optional leading `-`; any magnitude.
    Int(Box<str>),
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    /// Members in document order; a repeated name keeps its first position and its last value.
    Object(Vec<(String, Json)>),
}

fn bad(message: impl Into<String>) -> Error {
    Error::new("INVALID_JSON", message)
}

impl Json {
    pub fn int(value: impl Into<i128>) -> Self {
        Self::Int(value.into().to_string().into_boxed_str())
    }

    pub fn str(value: impl Into<String>) -> Self {
        Self::Str(value.into())
    }

    pub fn object<K: Into<String>>(members: impl IntoIterator<Item = (K, Json)>) -> Self {
        let mut object = Self::Object(Vec::new());
        for (key, value) in members {
            object.set(key, value);
        }
        object
    }

    /// Insert or replace a member, keeping the position of an existing name.
    pub fn set(&mut self, key: impl Into<String>, value: Json) {
        let key = key.into();
        if let Self::Object(members) = self {
            match members.iter_mut().find(|(name, _)| *name == key) {
                Some(member) => member.1 = value,
                None => members.push((key, value)),
            }
        }
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Self::Object(members) => members.iter().find(|(name, _)| name == key).map(|m| &m.1),
            _ => None,
        }
    }

    pub fn members(&self) -> Option<&[(String, Json)]> {
        match self {
            Self::Object(members) => Some(members),
            _ => None,
        }
    }

    pub fn items(&self) -> Option<&[Json]> {
        match self {
            Self::Array(items) => Some(items),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Str(text) => Some(text),
            _ => None,
        }
    }

    /// The value of an integer literal that fits in 64 bits; booleans and floats are not integers.
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Self::Int(digits) => digits.parse().ok(),
            _ => None,
        }
    }

    pub fn as_u64(&self) -> Option<u64> {
        self.as_i64().and_then(|value| u64::try_from(value).ok())
    }

    /// The member names of an object, in order.
    pub fn keys(&self) -> Vec<&str> {
        self.members()
            .map(|members| members.iter().map(|(name, _)| name.as_str()).collect())
            .unwrap_or_default()
    }

    /// True when the object has exactly the given member names, in any order.
    pub fn has_exactly(&self, names: &[&str]) -> bool {
        self.members().is_some_and(|members| {
            members.len() == names.len()
                && members
                    .iter()
                    .all(|(name, _)| names.contains(&name.as_str()))
        })
    }

    pub fn parse(bytes: &[u8]) -> Result<Json> {
        let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
        let text = std::str::from_utf8(bytes).map_err(|_| bad("document is not UTF-8"))?;
        let mut parser = Parser {
            text: text.as_bytes(),
            at: 0,
        };
        parser.space();
        let value = parser.value(0)?;
        parser.space();
        if parser.at != parser.text.len() {
            return Err(parser.error("extra data after the document"));
        }
        Ok(value)
    }

    /// The text `json.dump(value, indent=2)` writes, followed by one newline.
    pub fn to_pretty(&self) -> Result<String> {
        let mut out = String::new();
        self.write(&mut out, Some(0))?;
        out.push('\n');
        Ok(out)
    }

    /// One line with `", "` and `": "` separators, as `json.dumps(value)` writes it.
    pub fn to_line(&self) -> Result<String> {
        let mut out = String::new();
        self.write(&mut out, None)?;
        Ok(out)
    }

    fn write(&self, out: &mut String, level: Option<usize>) -> Result<()> {
        let open = |out: &mut String, level: Option<usize>| {
            if let Some(level) = level {
                out.push('\n');
                out.extend(std::iter::repeat_n("  ", level));
            }
        };
        match self {
            Self::Null => out.push_str("null"),
            Self::Bool(true) => out.push_str("true"),
            Self::Bool(false) => out.push_str("false"),
            Self::Int(digits) => out.push_str(digits),
            Self::Float(value) => out.push_str(&float_text(*value)?),
            Self::Str(text) => string_text(out, text),
            Self::Array(items) if items.is_empty() => out.push_str("[]"),
            Self::Object(members) if members.is_empty() => out.push_str("{}"),
            Self::Array(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_str(if level.is_some() { "," } else { ", " });
                    }
                    open(out, level.map(|l| l + 1));
                    item.write(out, level.map(|l| l + 1))?;
                }
                open(out, level);
                out.push(']');
            }
            Self::Object(members) => {
                out.push('{');
                for (index, (name, item)) in members.iter().enumerate() {
                    if index > 0 {
                        out.push_str(if level.is_some() { "," } else { ", " });
                    }
                    open(out, level.map(|l| l + 1));
                    string_text(out, name);
                    out.push_str(": ");
                    item.write(out, level.map(|l| l + 1))?;
                }
                open(out, level);
                out.push('}');
            }
        }
        Ok(())
    }
}

/// The shortest decimal digits that parse back to a positive finite float, and the decimal
/// exponent of the first digit. Among the shortest candidates the closest to the exact
/// value is taken; when two are equally close, the one whose last digit is even (the
/// reference's rule; the standard formatter rounds such a tie up).
fn shortest_digits(value: f64) -> (String, i32) {
    let split = |text: &str| -> (String, i32) {
        let (mantissa, exponent) = text.split_once('e').unwrap_or((text, "0"));
        (
            mantissa.chars().filter(char::is_ascii_digit).collect(),
            exponent.parse().unwrap_or(0),
        )
    };
    // `{:e}` prints the shortest digits that parse back to the same value.
    let (digits, exponent) = split(&format!("{value:e}"));
    let count = digits.len();
    // A tie needs an exact expansion one digit longer than a shortest form that still
    // identifies the float, which only happens near the 53-bit precision limit.
    if count < 15 {
        return (digits, exponent);
    }
    // 767 significant digits hold the exact value of any float.
    let (exact, exact_exponent) = split(&format!("{value:.800e}"));
    let exact = exact.as_bytes();
    if exact[count] != b'5' || exact[count + 1..].iter().any(|digit| *digit != b'0') {
        return (digits, exponent);
    }
    let mut even = exact[..count].to_vec();
    let mut even_exponent = exact_exponent;
    if (even[count - 1] - b'0') % 2 == 1 {
        let mut at = count;
        loop {
            if at == 0 {
                even.insert(0, b'1');
                even.pop();
                even_exponent += 1;
                break;
            }
            at -= 1;
            if even[at] == b'9' {
                even[at] = b'0';
            } else {
                even[at] += 1;
                break;
            }
        }
    }
    let mut candidate = String::from_utf8(even).unwrap_or_default();
    while candidate.len() > 1 && candidate.ends_with('0') {
        candidate.pop();
    }
    let text = format!("{}.{}e{even_exponent}", &candidate[..1], &candidate[1..]);
    if text.parse::<f64>().ok() == Some(value) {
        (candidate, even_exponent)
    } else {
        (digits, exponent)
    }
}

/// Shortest round-trip decimal text of a finite float, laid out as `repr(float)`:
/// positional for a decimal exponent in `-4..16`, else `d[.ddd]e±XX`.
pub fn float_text(value: f64) -> Result<String> {
    if !value.is_finite() {
        return Err(bad("out of range float values are not JSON compliant"));
    }
    if value == 0.0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into());
    }
    let (digits, exponent) = shortest_digits(value.abs());
    let point = exponent + 1; // digits before the decimal point
    let mut out = String::new();
    if value < 0.0 {
        out.push('-');
    }
    if point <= -4 || point > 16 {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let _ = write!(
            out,
            "e{}{:02}",
            if exponent < 0 { '-' } else { '+' },
            exponent.abs()
        );
    } else if point <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', point.unsigned_abs() as usize));
        out.push_str(&digits);
    } else if digits.len() as i32 <= point {
        out.push_str(&digits);
        out.extend(std::iter::repeat_n('0', point as usize - digits.len()));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..point as usize]);
        out.push('.');
        out.push_str(&digits[point as usize..]);
    }
    Ok(out)
}

fn string_text(out: &mut String, text: &str) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(character),
            _ => {
                let mut units = [0_u16; 2];
                for unit in character.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

struct Parser<'a> {
    text: &'a [u8],
    at: usize,
}

impl Parser<'_> {
    fn error(&self, message: &str) -> Error {
        bad(format!("{message} at byte {}", self.at))
    }

    fn space(&mut self) {
        while matches!(self.text.get(self.at), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }

    fn literal(&mut self, word: &str, value: Json) -> Result<Json> {
        if self.text[self.at..].starts_with(word.as_bytes()) {
            self.at += word.len();
            Ok(value)
        } else {
            Err(self.error("unexpected token"))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Json> {
        if depth > MAX_DEPTH {
            return Err(self.error("nesting depth budget exceeded"));
        }
        match self.text.get(self.at) {
            Some(b'n') => self.literal("null", Json::Null),
            Some(b't') => self.literal("true", Json::Bool(true)),
            Some(b'f') => self.literal("false", Json::Bool(false)),
            Some(b'"') => Ok(Json::Str(self.string()?)),
            Some(b'[') => {
                self.at += 1;
                let mut items = Vec::new();
                self.space();
                if self.text.get(self.at) == Some(&b']') {
                    self.at += 1;
                    return Ok(Json::Array(items));
                }
                loop {
                    self.space();
                    items.push(self.value(depth + 1)?);
                    self.space();
                    match self.text.get(self.at) {
                        Some(b',') => self.at += 1,
                        Some(b']') => {
                            self.at += 1;
                            return Ok(Json::Array(items));
                        }
                        _ => return Err(self.error("expected ',' or ']'")),
                    }
                }
            }
            Some(b'{') => {
                self.at += 1;
                let mut object = Json::Object(Vec::new());
                self.space();
                if self.text.get(self.at) == Some(&b'}') {
                    self.at += 1;
                    return Ok(object);
                }
                loop {
                    self.space();
                    if self.text.get(self.at) != Some(&b'"') {
                        return Err(self.error("expected a member name"));
                    }
                    let name = self.string()?;
                    self.space();
                    if self.text.get(self.at) != Some(&b':') {
                        return Err(self.error("expected ':'"));
                    }
                    self.at += 1;
                    self.space();
                    let value = self.value(depth + 1)?;
                    object.set(name, value);
                    self.space();
                    match self.text.get(self.at) {
                        Some(b',') => self.at += 1,
                        Some(b'}') => {
                            self.at += 1;
                            return Ok(object);
                        }
                        _ => return Err(self.error("expected ',' or '}'")),
                    }
                }
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err(self.error("expected a value")),
        }
    }

    fn digits(&mut self) -> usize {
        let start = self.at;
        while matches!(self.text.get(self.at), Some(b'0'..=b'9')) {
            self.at += 1;
        }
        self.at - start
    }

    fn number(&mut self) -> Result<Json> {
        let start = self.at;
        if self.text.get(self.at) == Some(&b'-') {
            self.at += 1;
        }
        let leading_zero = self.text.get(self.at) == Some(&b'0');
        let whole = self.digits();
        if whole == 0 || (leading_zero && whole > 1) {
            return Err(self.error("invalid number"));
        }
        let mut integer = true;
        if self.text.get(self.at) == Some(&b'.') {
            self.at += 1;
            integer = false;
            if self.digits() == 0 {
                return Err(self.error("invalid number"));
            }
        }
        if matches!(self.text.get(self.at), Some(b'e' | b'E')) {
            self.at += 1;
            integer = false;
            if matches!(self.text.get(self.at), Some(b'+' | b'-')) {
                self.at += 1;
            }
            if self.digits() == 0 {
                return Err(self.error("invalid number"));
            }
        }
        // The slice holds ASCII digits, sign, point and exponent only.
        let lexeme = std::str::from_utf8(&self.text[start..self.at]).unwrap_or("0");
        if integer {
            let digits = if lexeme == "-0" { "0" } else { lexeme };
            Ok(Json::Int(digits.into()))
        } else {
            let value: f64 = lexeme.parse().map_err(|_| self.error("invalid number"))?;
            Ok(Json::Float(value))
        }
    }

    fn hex4(&mut self) -> Result<u32> {
        let digits = self
            .text
            .get(self.at..self.at + 4)
            .and_then(|slice| std::str::from_utf8(slice).ok())
            .filter(|slice| slice.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| self.error("invalid \\u escape"))?;
        self.at += 4;
        u32::from_str_radix(digits, 16).map_err(|_| self.error("invalid \\u escape"))
    }

    fn string(&mut self) -> Result<String> {
        self.at += 1; // opening quote
        let mut out = String::new();
        loop {
            let start = self.at;
            while let Some(&byte) = self.text.get(self.at) {
                if byte == b'"' || byte == b'\\' || byte < 0x20 {
                    break;
                }
                self.at += 1;
            }
            // Splits only at ASCII bytes, so the slice stays valid UTF-8.
            out.push_str(std::str::from_utf8(&self.text[start..self.at]).unwrap_or(""));
            match self.text.get(self.at) {
                Some(b'"') => {
                    self.at += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escape = *self
                        .text
                        .get(self.at)
                        .ok_or_else(|| self.error("unterminated string"))?;
                    self.at += 1;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.hex4()?;
                            if (0xd800..0xdc00).contains(&code)
                                && self.text[self.at..].starts_with(b"\\u")
                            {
                                let mark = self.at;
                                self.at += 2;
                                let low = self.hex4()?;
                                if (0xdc00..0xe000).contains(&low) {
                                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                                } else {
                                    self.at = mark;
                                }
                            }
                            out.push(char::from_u32(code).ok_or_else(|| {
                                self.error("unpaired surrogate escapes are not supported")
                            })?);
                        }
                        _ => return Err(self.error("invalid escape")),
                    }
                }
                Some(_) => return Err(self.error("control character in a string")),
                None => return Err(self.error("unterminated string")),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// FLOWEXEC-REQ-0008: shortest round-trip digits, even last digit on a tie, reference layout.
    #[test]
    fn floats_print_as_the_reference_repr() {
        // Expected texts are `repr(float(x))` of CPython 3.13.
        for (value, expected) in [
            (1.0, "1.0"),
            (-1.5, "-1.5"),
            (0.1, "0.1"),
            (0.075, "0.075"),
            (100.0, "100.0"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (123456789012345678.0, "1.2345678901234568e+17"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.25e-7, "1.25e-07"),
            (1e100, "1e+100"),
            (1e-100, "1e-100"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (2.5, "2.5"),
            (0.30000000000000004, "0.30000000000000004"),
            (9007199254740993.0, "9007199254740992.0"),
            (12345.678, "12345.678"),
            (433.3, "433.3"),
            (0.15012345678, "0.15012345678"),
            // Exact ties between two shortest forms: the even last digit is taken.
            (987067669750643.0 + 0.25, "987067669750643.2"),
            (229686820739483.0 + 0.625, "229686820739483.62"),
            (2474258308253.0 + 0.65625, "2474258308253.6562"),
            (1655533191724344.0 + 0.25, "1655533191724344.2"),
            (1450635738979107.0 + 0.25, "1450635738979107.2"),
            (987067669750643.0 + 0.75, "987067669750643.8"),
            (5e-324, "5e-324"),
            (1e23, "1e+23"),
            (9.5e15, "9500000000000000.0"),
        ] {
            assert_eq!(float_text(value).unwrap(), expected, "{value:e}");
        }
        assert_eq!(float_text(-0.0).unwrap(), "-0.0");
        assert!(float_text(f64::NAN).is_err());
        assert!(float_text(f64::INFINITY).is_err());
    }

    #[test]
    fn numbers_keep_the_integer_and_float_distinction() {
        let value =
            Json::parse(br#"[1, 1.0, 1e3, -0, 12345678901234567890123, 1E-7, -2.50]"#).unwrap();
        assert_eq!(
            value.to_line().unwrap(),
            "[1, 1.0, 1000.0, 0, 12345678901234567890123, 1e-07, -2.5]"
        );
        assert_eq!(Json::parse(b"true").unwrap().as_i64(), None);
        assert_eq!(Json::parse(b"1.0").unwrap().as_i64(), None);
        assert_eq!(Json::parse(b"7").unwrap().as_i64(), Some(7));
        for invalid in [
            "01",
            "1.",
            ".5",
            "+1",
            "1e",
            "NaN",
            "Infinity",
            "-",
            "[1,]",
            "{\"a\":1,}",
        ] {
            assert!(Json::parse(invalid.as_bytes()).is_err(), "{invalid}");
        }
    }

    /// FLOWEXEC-REQ-0008: two-space indent, member order kept, non-ASCII escaped.
    #[test]
    fn pretty_text_matches_the_reference_writer() {
        let value = Json::parse(
            "{\"b\": [], \"a\": {}, \"s\": \"\u{e9}\\u00e9\\ud83d\\ude00\\u007f\\n\\\"/\", \
             \"n\": null, \"l\": [1, [true, false]], \"a\": {\"k\": 0.5}}"
                .as_bytes(),
        )
        .unwrap();
        let expected = "{\n  \"b\": [],\n  \"a\": {\n    \"k\": 0.5\n  },\n  \"s\": \
                        \"\\u00e9\\u00e9\\ud83d\\ude00\\u007f\\n\\\"/\",\n  \"n\": null,\n  \
                        \"l\": [\n    1,\n    [\n      true,\n      false\n    ]\n  ]\n}\n";
        assert_eq!(value.to_pretty().unwrap(), expected);
        assert_eq!(Json::parse(b"[]").unwrap().to_pretty().unwrap(), "[]\n");
    }

    #[test]
    fn malformed_documents_are_rejected() {
        for invalid in [
            "",
            "{",
            "[1 2]",
            "{\"a\" 1}",
            "\"\x01\"",
            "\"\\x\"",
            "\"\\ud800\"",
            "1 2",
            "nul",
        ] {
            assert_eq!(
                Json::parse(invalid.as_bytes()).unwrap_err().code,
                "INVALID_JSON"
            );
        }
        let deep = "[".repeat(600) + &"]".repeat(600);
        assert!(Json::parse(deep.as_bytes()).is_err());
        assert_eq!(
            Json::parse(b"\xef\xbb\xbf {\"a\": 1} ").unwrap().keys(),
            ["a"]
        );
    }
}
