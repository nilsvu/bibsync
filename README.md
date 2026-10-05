# bibsync

Keeps BibTeX entries identical to their records in online databases, so no
bibliographic field is typed by hand (or by an AI agent).

Pulls entries from these databases, in this order:

1. **INSPIRE-HEP**: looked up by the citation key if it is an INSPIRE texkey
   (`Einstein:1916vd`), otherwise by the entry's `doi`, then its `eprint`
2. **doi.org**: Crossref for journals, DataCite for Zenodo software and data,
   looked up by `doi`
3. **arXiv**: looked up by `eprint`

bibsync writes the fetched BibTeX data into the `.bib` file you give it. The
BibTeX fields named by `--keep` (default: `keywords,note`) are not overwritten,
so you can keep your own notes and keywords.

Some entries are reported as *unmanaged* and left untouched, so you can maintain
them by hand:

- entries without a texkey, `doi` or `eprint`, such as websites
- entries marked `bibsync = {manual}`, for when a database's record is broken

Formatting choices come with your biblatex style, not by editing the `.bib`. For
example, `\clearfield{language}` hides the `language = {en}` that Zenodo records
carry.

## Install

bibsync needs Python 3.11 or newer. From a clone of this repository:

```sh
pipx install .        # or: uv tool install .
```

For development:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'   # or '.[test]' without black
```

## Usage

```sh
bibsync add     refs.bib Einstein:1916vd  # INSPIRE texkey
bibsync add     refs.bib 1602.03837       # arXiv id: INSPIRE's texkey becomes the key
bibsync add     refs.bib 10.7935/GT1W-FZ16 --key lalsuite  # not on INSPIRE: pass a name
bibsync check   refs.bib                  # exit 1 if any entry differs from its online record
bibsync update  refs.bib [KEY ...]        # rewrite the entries that differ
bibsync update  refs.bib --keep keywords,note  # don't overwrite these fields (default)
bibsync missing refs.bib *.tex            # cited keys that refs.bib lacks
bibsync missing refs.bib *.tex --add      # ...and fetch those that are texkeys
bibsync prune   refs.bib *.tex            # entries that no TeX file cites
bibsync prune   refs.bib *.tex --remove   # ...and delete them
```

`prune` uses the same citation scan as `missing` (below). Pass every TeX file
that uses the bibliography. It refuses to run if one of them has `\nocite{*}`,
since then every entry counts as cited.

`missing` reads every `\...cite...{keys}` command, including custom macros like
`\ccite`, capitalized ones like `\Textcite` and multicite commands like
`\cites{A}{B}`, and skips commented-out lines and `\nocite{*}`. So you can cite
a new paper by its INSPIRE texkey and run `missing --add`. Keys that aren't
texkeys are listed with the `add ... --key` command to run, and `missing` exits
1 while any cited key is still absent.

`check` prints a diff for every entry that differs and writes nothing. Run it
before `update` and before submitting. If INSPIRE has a record, `add` uses its
texkey as the citation key.

## What the databases give you

- INSPIRE's BibTeX lists only the first author followed by `and others` for
  papers with more than 10 authors. Mark an entry `bibsync = {full-authors}`, or
  add it with `bibsync add --full-authors`, to get INSPIRE's complete author
  list from the record's JSON instead. Use this for papers whose author list you
  want shown in full; collaboration papers would bring in hundreds of names.
- arXiv titles carry no brace protection, while INSPIRE wraps the whole title in
  braces.
- A Zenodo concept DOI describes the latest software version (its year and
  author list).
- Entry types follow the source, for example `@misc` for arXiv preprints.

## Tests

```sh
.venv/bin/python -m pytest                    # offline, with recorded responses
BIBSYNC_ONLINE=1 .venv/bin/python -m pytest   # also query the real databases
```

Run the online test now and then: it fails if a database changes its API or
its BibTeX format. Format the code with `.venv/bin/black .`.
