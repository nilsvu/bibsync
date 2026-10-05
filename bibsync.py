#!/usr/bin/env python3
"""Keep BibTeX entries identical to their records in online databases.

A managed entry is the BibTeX that INSPIRE-HEP, the DOI registrar (Crossref or
DataCite, via doi.org) or arXiv returns for it. bibsync sets the citation key,
puts one field per line, and takes the fields named by --keep (default:
keywords,note) from your file instead of the database. It never writes a field
value of its own.

The record for an entry is looked up, in this order, by:
  1. INSPIRE: the citation key if it is an INSPIRE texkey (Author:2021abc),
     then the entry's doi, then its eprint
  2. doi.org: the entry's doi
  3. arXiv: the entry's eprint
A record whose doi and eprint both contradict the entry is refused. Entries
without a texkey, doi or eprint, and entries with `bibsync = {manual}` (for
broken database records), are reported as unmanaged and left alone.

INSPIRE's BibTeX lists only the first of more than 10 authors. For entries with
`bibsync = {full-authors}`, the author field is INSPIRE's complete list from the
record's JSON instead, with the names exactly as INSPIRE stores them.

Usage:
  bibsync add     BIB ID [--key KEY] [--full-authors]
                                          ID: INSPIRE texkey, arXiv id or DOI
  bibsync check   BIB [KEY ...]           exit 1 if an entry differs from its record
  bibsync update  BIB [KEY ...]           rewrite the entries that differ
  bibsync missing BIB TEX... [--add]      list keys cited in TEX but not in BIB;
                                          --add fetches those that are texkeys
  bibsync prune   BIB TEX... [--remove]   list entries of BIB that TEX doesn't
                                          cite; --remove deletes them
"""

import argparse
import difflib
import json
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

__version__ = "1.0.0"

TEXKEY = re.compile(r"^[A-Za-z][A-Za-z0-9'.\-]*:\d{4}[a-z]{2,3}$")
ARXIV = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")
DOI = re.compile(r"^10\.\d{4,9}/\S+$")
INSPIRE_API = "https://inspirehep.net/api"
BIBTEX = "application/x-bibtex"


# Errors that are reported as a message, without a traceback: LookupError for
# records that can't be found, ValueError for bad input, OSError for network and
# file errors (including requests' errors).
FAILURES = (LookupError, ValueError, OSError)


# BibTeX parsing ------------------------------------------------------------

ENTRY_START = re.compile(r"@(\w+)\s*\{")
ENTRY_KEY = re.compile(r"\s*([^,\s}]+)\s*,?")
FIELD_NAME = re.compile(r"([^\s=,{}\"]+)\s*=\s*")
BARE_VALUE = re.compile(r"[^\s,#}]+")
CONCAT = re.compile(r"\s*#\s*")
SEPARATOR = re.compile(r"[\s,]*")


@dataclass
class Entry:
    type: str
    key: str
    fields: list  # [(name, raw value including its delimiters)]
    raw: str  # the entry's text, from "@" to the closing brace

    def get(self, name):
        """The value of a field without its outer delimiters, or None."""
        for n, v in self.fields:
            if n.lower() == name.lower():
                return v[1:-1] if v[:1] in '{"' else v
        return None


def _close(s, i):
    """Index after the delimiter that closes the brace or quote at s[i]."""
    closing, depth = s[i] if s[i] == '"' else "}", 0
    for j in range(i + 1, len(s)):
        if s[j] == closing and depth == 0:
            return j + 1
        depth += {"{": 1, "}": -1}.get(s[j], 0)
    raise ValueError(f"unbalanced {s[i]} at {s[i:i + 40]!r}")


def _value_end(s, j):
    """Index after the field value starting at s[j], including # concatenation."""
    while True:
        if s[j : j + 1] in ("{", '"'):
            j = _close(s, j)
        elif m := BARE_VALUE.match(s, j):
            j = m.end()
        else:
            raise ValueError(f"cannot read value at {s[j:j + 40]!r}")
        if not (m := CONCAT.match(s, j)):
            return j
        j = m.end()


def parse(text):
    """Split BibTeX into a list of strings (kept verbatim) and Entry objects."""
    out, i = [], 0
    while m := ENTRY_START.search(text, i):
        end = _close(text, m.end() - 1)
        if m.group(1).lower() in ("comment", "string", "preamble"):
            out.append(text[i:end])
            i = end
            continue
        out.append(text[i : m.start()])
        body = text[m.end() : end - 1]
        if not (k := ENTRY_KEY.match(body)):
            raise ValueError(f"cannot read the key at {text[m.start():][:40]!r}")
        fields, j = [], k.end()
        while (j := SEPARATOR.match(body, j).end()) < len(body):
            if not (f := FIELD_NAME.match(body, j)):
                raise ValueError(
                    f"cannot read a field of {k.group(1)} at {body[j:j + 40]!r}"
                )
            v = _value_end(body, f.end())
            fields.append((f.group(1), body[f.end() : v]))
            j = v
        out.append(Entry(m.group(1), k.group(1), fields, text[m.start() : end]))
        i = end
    out.append(text[i:])
    return [x for x in out if x != ""]


def entries(items):
    return [x for x in items if isinstance(x, Entry)]


def render(type, key, fields):
    return "\n".join([f"@{type}{{{key},", *(f"  {n} = {v}," for n, v in fields), "}"])


# Databases -----------------------------------------------------------------

session = requests.Session()
session.headers["User-Agent"] = f"bibsync/{__version__}"
session.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504)
        )
    ),
)
_last_request = 0.0


def http_get(url, accept):
    """The response body, or None if the database has no such record."""
    global _last_request
    time.sleep(max(0.0, _last_request + 0.4 - time.time()))  # INSPIRE: 15 per 5 s
    try:
        # arXiv refuses requests without Accept
        r = session.get(url, headers={"Accept": accept}, timeout=60)
    finally:
        _last_request = time.time()
    # doi.org redirects to the landing page if the registrar has no BibTeX
    if r.status_code in (400, 404, 410) or "html" in r.headers.get("Content-Type", ""):
        return None
    r.raise_for_status()
    return r.content.decode("utf-8")


def inspire_search(texkey, **params):
    query = {"q": f'texkeys:"{texkey}"', **params, "size": 2}
    return f"{INSPIRE_API}/literature?" + urllib.parse.urlencode(query)


def norm_doi(s):
    return re.sub(
        r"^(https?://(dx\.)?doi\.org/|doi:)", "", s.strip(), flags=re.I
    ).lower()


def norm_eprint(s):
    return re.sub(r"v\d+$", "", re.sub(r"^arxiv:", "", s.strip(), flags=re.I))


def identifiers(entry):
    doi, eprint = entry.get("doi"), entry.get("eprint")
    return (norm_doi(doi) if doi else None, norm_eprint(eprint) if eprint else None)


def resolve(key, doi=None, eprint=None):
    """(source, Entry) from the first database that has the record, or None."""
    q = lambda s: urllib.parse.quote(s, safe="/")
    lookups = [
        ("INSPIRE", TEXKEY.match(key) and inspire_search(key, format="bibtex"), "*/*"),
        ("INSPIRE", doi and f"{INSPIRE_API}/doi/{q(doi)}", BIBTEX),
        ("INSPIRE", eprint and f"{INSPIRE_API}/arxiv/{q(eprint)}", BIBTEX),
        ("doi.org", doi and f"https://doi.org/{q(doi)}", BIBTEX),
        ("arXiv", eprint and f"https://arxiv.org/bibtex/{q(eprint)}", "*/*"),
    ]
    for source, url, accept in lookups:
        if not url:
            continue
        records = entries(parse(http_get(url, accept) or ""))
        if len(records) > 1:
            raise LookupError(f"{source} returned {len(records)} records for {url}")
        if records:
            return source, records[0]
    return None


def consistent(ids, record_ids):
    """False if the entry and the record share no doi or eprint they agree on."""
    pairs = [(a, b) for a, b in zip(ids, record_ids) if a and b]
    return not pairs or any(a == b for a, b in pairs)


def full_authors(record):
    """The INSPIRE record with the complete author list from its JSON."""
    data = http_get(
        inspire_search(record.key, fields="authors.full_name"), "application/json"
    )
    hits = json.loads(data)["hits"]["hits"] if data else []
    authors = hits[0]["metadata"].get("authors", []) if len(hits) == 1 else []
    names = [a["full_name"] for a in authors]
    if not names or any(c in n for n in names for c in "{}"):
        raise LookupError(f"INSPIRE has no usable author list for {record.key}")
    author = "{" + " and ".join(names) + "}"
    fields = [(n, author if n.lower() == "author" else v) for n, v in record.fields]
    return replace(record, fields=fields)


def lookup(key, ids, full=False):
    """(source, record) for an entry with this key and (doi, eprint)."""
    found = resolve(key, *ids)
    if not found:
        raise LookupError("no database has this record")
    source, record = found
    if not consistent(ids, identifiers(record)):
        raise LookupError(
            f"{source} record {record.key} has a different doi and eprint"
        )
    if full and source == "INSPIRE":
        record = full_authors(record)
    return source, record


def merge(record, key, local, keep):
    """The entry to store: the record's fields with the --keep fields from `local`."""
    keep = keep | {"bibsync"}  # bibsync's own per-entry setting
    fields = [(n, v) for n, v in record.fields if n.lower() not in keep]
    fields += [(n, v) for n, v in (local.fields if local else []) if n.lower() in keep]
    return render(record.type, key, fields)


# Commands ------------------------------------------------------------------


def sync(path, keys, keep, write):
    items = parse(Path(path).read_text(encoding="utf-8"))
    unknown = set(keys) - {e.key for e in entries(items)}
    if unknown:
        raise LookupError(f"not in {path}: {', '.join(sorted(unknown))}")
    count = {"ok": 0, "differs": 0, "unmanaged": 0, "failed": 0}
    for e in entries(items):
        if keys and e.key not in keys:
            continue
        ids, mode = identifiers(e), e.get("bibsync")
        if mode == "manual" or not (TEXKEY.match(e.key) or any(ids)):
            count["unmanaged"] += 1
            why = (
                "bibsync = {manual}"
                if mode == "manual"
                else "no INSPIRE texkey, doi or eprint"
            )
            print(f"unmanaged  {e.key}: {why}")
            continue
        try:
            if mode not in (None, "full-authors"):
                raise ValueError(f"unknown setting bibsync = {{{mode}}}")
            source, record = lookup(e.key, ids, full=mode == "full-authors")
        except FAILURES as err:
            count["failed"] += 1
            print(f"FAILED     {e.key}: {err}")
            continue
        new = merge(record, e.key, e, keep)
        if new == e.raw:
            count["ok"] += 1
            print(f"ok         {e.key} ({source})")
            continue
        count["differs"] += 1
        print(f"{'updated' if write else 'DIFFERS':10s} {e.key} ({source})")
        diff = difflib.unified_diff(
            e.raw.splitlines(), new.splitlines(), lineterm="", n=0
        )
        for line in list(diff)[2:]:
            print(f"           {line}")
        e.raw = new
    if write and count["differs"]:
        Path(path).write_text(
            "".join(x if isinstance(x, str) else x.raw for x in items), encoding="utf-8"
        )
    print(", ".join(f"{n} {k}" for k, n in count.items()))
    return 1 if count["failed"] or (count["differs"] and not write) else 0


def add(path, ident, key, keep, full=False):
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if TEXKEY.match(ident):
        source, record = lookup(ident, (None, None), full)
    elif DOI.match(norm_doi(ident)):
        source, record = lookup("", (norm_doi(ident), None), full)
    elif ARXIV.match(norm_eprint(ident)):
        source, record = lookup("", (None, norm_eprint(ident)), full)
    else:
        raise ValueError(f"{ident} is not an INSPIRE texkey, DOI or arXiv id")
    if source == "INSPIRE":
        texkey = ident if TEXKEY.match(ident) else record.key
        if key and key != texkey:
            raise ValueError(
                f"INSPIRE has this record as {texkey}; cite it with that key"
            )
        key = texkey
    elif not key:
        raise ValueError(
            f"{ident} is not on INSPIRE ({source} has it); choose a citation key with --key"
        )
    if key in {e.key for e in entries(parse(text))}:
        raise ValueError(f"{key} is already in {path}")
    local = Entry("", "", [("bibsync", "{full-authors}")], "") if full else None
    entry = merge(record, key, local, keep)
    path.write_text(
        text.rstrip("\n") + ("\n\n" if text.strip() else "") + entry + "\n",
        encoding="utf-8",
    )
    print(f"added {key} from {source}:\n{entry}")
    return 0


# Any \...cite...{keys} command, including custom ones like \ccite and
# capitalized ones like \Textcite, with optional [args]. Multicite commands like
# \cites[p.~3]{A}{B} and \textcites(pre)(post){A}[]{B} have several key groups.
CITE = re.compile(
    r"\\([A-Za-z]*[Cc]ite[A-Za-z]*)\*?((?:\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}))*)"
)


def tex_text(f):
    """The TeX file's text without comments."""
    return re.sub(r"(?<!\\)%.*", "", Path(f).read_text(encoding="utf-8"))


def cited_keys(tex_files):
    """Citation keys in the TeX files, in order of first use, ignoring comments,
    \\nocite{*} and macro parameters like the #1 in \\newcommand{\\ccite}[1]{\\cite{#1}}.
    """
    keys = []
    for f in tex_files:
        for m in CITE.finditer(tex_text(f)):
            groups = re.findall(r"\{([^}]*)\}", m.group(2))
            for group in groups if m.group(1).lower().endswith("cites") else groups[:1]:
                keys += [k.strip() for k in group.split(",")]
    keys = [k for k in keys if k and k != "*" and "#" not in k]
    return list(dict.fromkeys(keys))


def missing(path, tex_files, add_them, keep):
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    have = {e.key for e in entries(parse(text))}
    left = 0
    for key in (k for k in cited_keys(tex_files) if k not in have):
        if not TEXKEY.match(key):
            left += 1
            print(
                f"missing    {key}: not a texkey; run bibsync add {path} ID --key {key}"
            )
            continue
        if not add_them:
            left += 1
            print(f"missing    {key}")
            continue
        try:
            add(path, key, None, keep)
        except FAILURES as err:
            left += 1
            print(f"FAILED     {key}: {err}")
    return 1 if left else 0


def prune(path, tex_files, remove):
    for f in tex_files:
        if re.search(r"\\nocite\{\*\}", tex_text(f)):
            raise ValueError(
                f"{f} cites every entry with \\nocite{{*}}; nothing to prune"
            )
    items = parse(Path(path).read_text(encoding="utf-8"))
    cited = set(cited_keys(tex_files))
    uncited = [e for e in entries(items) if e.key not in cited]
    for e in uncited:
        print(f"{'removed' if remove else 'uncited':10s} {e.key}")
    if remove and uncited:
        out = []
        for prev, x in zip([None, *items], items):
            if isinstance(x, str):  # text after a removed entry loses its blank lines
                out.append(x.lstrip() if prev in uncited else x)
            elif x not in uncited:
                out.append(x.raw)
        Path(path).write_text("".join(out).rstrip() + "\n", encoding="utf-8")
    return 1 if uncited and not remove else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", action="version", version=f"bibsync {__version__}")
    keep = argparse.ArgumentParser(add_help=False)
    keep.add_argument(
        "--keep",
        default="keywords,note",
        type=lambda s: {f.strip().lower() for f in s.split(",") if f.strip()},
        metavar="FIELDS",
        help="comma-separated fields taken from your file, not the database"
        " (default: keywords,note)",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "add",
        parents=[keep],
        help="add the record for an INSPIRE texkey, arXiv id or DOI",
    )
    p.add_argument("bib")
    p.add_argument("id")
    p.add_argument("--key", help="citation key, for records that are not on INSPIRE")
    p.add_argument(
        "--full-authors",
        action="store_true",
        help="mark the entry bibsync = {full-authors}: all authors, not the first of >10",
    )
    for name, desc in (
        ("check", "compare entries with their records"),
        ("update", "rewrite entries that differ from their records"),
    ):
        p = sub.add_parser(name, parents=[keep], help=desc)
        p.add_argument("bib")
        p.add_argument("keys", nargs="*", help="only these entries")
    p = sub.add_parser(
        "missing", parents=[keep], help="list keys cited in TeX files but not in BIB"
    )
    p.add_argument("bib")
    p.add_argument("tex", nargs="+")
    p.add_argument("--add", action="store_true", help="fetch the missing texkeys")
    p = sub.add_parser("prune", help="list entries of BIB that no TeX file cites")
    p.add_argument("bib")
    p.add_argument("tex", nargs="+")
    p.add_argument("--remove", action="store_true", help="delete the uncited entries")
    args = ap.parse_args(argv)
    try:
        if args.command == "add":
            return add(args.bib, args.id, args.key, args.keep, args.full_authors)
        if args.command == "missing":
            return missing(args.bib, args.tex, args.add, args.keep)
        if args.command == "prune":
            return prune(args.bib, args.tex, args.remove)
        return sync(args.bib, args.keys, args.keep, write=args.command == "update")
    except FAILURES as err:
        sys.exit(f"bibsync: {err}")


if __name__ == "__main__":
    sys.exit(main())
