<img src="assets/icon.svg" width="64" height="64" alt="">

# dwgcheck

A local CLI that reports whether DWG files are structurally valid and uncorrupted.
It only detects problems. It never repairs or modifies input files.

Supported: **AC1024 (2010), AC1027 (2013), AC1032 (2018+)**. Any other version is reported as
`unsupported` and is not validated.

## What "valid" means here

`valid` means the file's container is internally consistent: the encrypted file header, the section
page map, the section map and every data page decode cleanly, every checksum and CRC matches, every
page decompresses to its declared size, the required sections exist, and the file ends exactly where
its own header says it does.

It does **not** mean "guaranteed to open in AutoCAD". Tier 1 does not interpret drawing objects.
Tier 2 (`--deep`) goes further by having the ODA File Converter read the whole drawing and auditing
the result, but it is still an approximation of AutoCAD's behaviour (see [Known gaps](#known-gaps)).

## Step-by-step: macOS and Linux

On Windows? Skip to [Step-by-step: Windows](#step-by-step-windows).

### 1. Install uv

uv manages Python and the project's dependencies. If `uv --version` already works, skip this step.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal afterwards so `uv` is on your `PATH`.

### 2. Get the code and install it

```sh
git clone https://github.com/realDragonium/dwg-verifier.git
cd dwg-verifier
uv sync --extra deep     # creates .venv with Python 3.11+, dwgcheck, ezdxf and the test tools
```

`uv sync` without `--extra deep` also works. Tier 1 uses only the Python standard library; only
`--deep` needs ezdxf.

### 3. Check a file (Tier 1)

```sh
uv run dwgcheck fixtures/libredwg/example_2018.dwg
# OK       fixtures/libredwg/example_2018.dwg (AC1032) [T1]

uv run dwgcheck fixtures -r          # a whole directory tree
```

To use plain `dwgcheck` instead of `uv run dwgcheck`, activate the project environment first:
`source .venv/bin/activate`.

### 4. Optional: install the ODA File Converter for `--deep`

Tier 2 needs the free ODA File Converter. Download it from
<https://www.opendesign.com/guestfiles/oda_file_converter>.

| OS | Install | Where dwgcheck finds it |
|---|---|---|
| macOS | Open the `.dmg` (arm64 for Apple Silicon, x64 for Intel) and drag `ODAFileConverter.app` into `/Applications`. | `/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter` |
| Linux | `sudo apt install ./ODAFileConverter_QT6_lnxX64_*.deb` (or the `.rpm`). On a machine without a display, also `sudo apt install xvfb`. | `ODAFileConverter` on `PATH`, `/usr/bin`, `/usr/local/bin` |

If it is installed elsewhere, point dwgcheck at the executable:

```sh
export DWGCHECK_ODAFC=/path/to/ODAFileConverter
```

On Linux without `DISPLAY`, dwgcheck runs the converter under `xvfb-run -a`. If xvfb is missing,
`--deep` exits with code 3 and tells you so. It never silently skips Tier 2.

### 5. Check with both tiers

```sh
uv run dwgcheck fixtures -r --deep
# OK       fixtures/libredwg/2010/Arc.dwg (AC1024) [T1+T2]
```

### 6. Optional (macOS): run the converter without its window

The stock macOS converter opens a window for every file it converts. Qt's `offscreen` platform
plugin avoids that, but ODA doesn't bundle it. You can add it to a private copy of the app. The
plugin must match the converter's Qt version: check it with
`otool -L /Applications/ODAFileConverter.app/Contents/Frameworks/QtCore.framework/QtCore | sed -n 2p`
(the converter tested here reports `current version 6.9.3`).

```sh
mkdir -p /tmp/qt-offscreen && cd /tmp/qt-offscreen
uv run --no-project --with pip python -m pip download --no-deps --only-binary=:all: \
    --platform macosx_12_0_universal2 --python-version 3.12 "PySide6-Essentials==6.9.3" -d .
unzip -o -q pyside6_essentials-*.whl "PySide6/Qt/plugins/platforms/libqoffscreen.dylib"

mkdir -p ~/Applications
cp -R /Applications/ODAFileConverter.app ~/Applications/ODAFileConverter-headless.app
cp PySide6/Qt/plugins/platforms/libqoffscreen.dylib \
   ~/Applications/ODAFileConverter-headless.app/Contents/PlugIns/platforms/
# The app uses the hardened runtime, which refuses a plugin signed by someone else. Re-sign the
# copy ad hoc (this replaces ODA's signature on the copy only).
codesign --force --deep --sign - ~/Applications/ODAFileConverter-headless.app

export DWGCHECK_ODAFC=~/Applications/ODAFileConverter-headless.app/Contents/MacOS/ODAFileConverter
```

When the converter's bundle contains `libqoffscreen`, dwgcheck sets `QT_QPA_PLATFORM=offscreen` on
its own. The headless copy produced the same DXF output as the stock app (apart from the
timestamps that differ on every run anyway) and the same error reports, and it was about twice as
fast. Don't set `QT_QPA_PLATFORM=offscreen` against the stock app: the converter aborts and macOS
shows a crash dialog.

### 7. Run the tests

```sh
uv run pytest                 # 101 tests, ~2 s without the converter, ~17 s with it
uv run mypy                   # strict type check
uv run pytest -m "not deep"   # skip the Tier 2 tests explicitly
```

Tier 2 tests are skipped, with the reason shown, when the converter isn't found. Put your own
drawings in `fixtures/local/` (gitignored): every test that runs over the fixtures picks them up and
expects them to be valid.

### 8. Regenerate the detection table

```sh
uv run python -m tools.detection_table --deep   # ~45 s with the headless converter
uv run python -m tools.corruptions some.dwg out/  # write the corrupted variants to disk
```

## Step-by-step: Windows

Every command below goes into **PowerShell**: press the Windows key, type `PowerShell`, press
Enter. Paste one block at a time. You don't need to install Python yourself; uv takes care of it.

> These steps follow the documented Windows behaviour of uv, Git and the ODA File Converter and
> dwgcheck's Windows code path, but they haven't been run on a Windows machine yet. If a step
> doesn't do what it says, the [troubleshooting table](#windows-troubleshooting) covers the
> likely causes.

### 1. Install Git and uv

```powershell
winget install --id Git.Git -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

If Windows asks whether to allow changes, choose **Yes**. Then **close PowerShell and open a new
one**, so it picks up the new commands, and check:

```powershell
git --version
uv --version
```

Both should print a version number. (Already have Git? The first line just says it's installed.)

### 2. Download dwgcheck and set it up

```powershell
cd $HOME
git clone https://github.com/realDragonium/dwg-verifier.git
cd dwg-verifier
uv sync --extra deep
```

The last command downloads Python and everything dwgcheck needs into a `.venv` folder inside
`dwg-verifier`. It takes a minute the first time, and nothing is installed system-wide.

From now on, always run dwgcheck from inside this folder. In a new PowerShell window, go back with
`cd $HOME\dwg-verifier`.

### 3. Check that it works

```powershell
uv run dwgcheck fixtures -r
```

You should see a line for each sample drawing, all starting with `OK`, for example:

```
OK       fixtures\libredwg\2010\Arc.dwg (AC1024) [T1]
```

### 4. Check your own drawings

Put the path in quotes, which is needed whenever it contains spaces:

```powershell
uv run dwgcheck "C:\Users\you\Documents\Drawings\plan.dwg"     # one file
uv run dwgcheck "C:\Users\you\Documents\Drawings"              # all .dwg files in a folder
uv run dwgcheck "C:\Users\you\Documents\Drawings" -r           # ... and all its subfolders
```

Tip: in File Explorer, hold Shift, right-click a file or folder and choose **Copy as path**, then
paste it after `uv run dwgcheck`. It's already quoted.

The result is on screen: `OK`, `INVALID` (followed by what is wrong) or `UNSUPPORTED` (a DWG
version dwgcheck doesn't validate). For scripts, `echo $LASTEXITCODE` right after a run prints the
[exit code](#exit-codes).

### 5. Optional: install the ODA File Converter for deep checks

`--deep` also has the ODA File Converter read each whole drawing. It's free:

1. Open <https://www.opendesign.com/guestfiles/oda_file_converter> and download the **Windows**
   version (a file ending in `.msi`, for example `ODAFileConverter_QT6_vc16_amd64dll.msi`).
2. Double-click the downloaded file and click through the installer with the default options. If
   Windows SmartScreen warns about the file, choose **More info**, then **Run anyway**.
3. Check that dwgcheck finds it:

   ```powershell
   uv run dwgcheck fixtures -r --deep
   ```

   Every line should now end in `[T1+T2]`. Expect this to take a few seconds per file. The
   converter's window may flash up for each drawing; leave it alone, it closes by itself.

If you see `ODA File Converter not found`, the installer used a folder dwgcheck doesn't look in
(it looks in `C:\Program Files\ODA\ODAFileConverter*\`). Find the program and tell dwgcheck where
it is:

```powershell
Get-ChildItem "C:\Program Files", "C:\Program Files (x86)" -Recurse -Filter ODAFileConverter.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
```

Put the path it prints between the quotes of this command, in place of the example path:

```powershell
setx DWGCHECK_ODAFC "C:\Program Files\ODA\ODAFileConverter 27.9.0\ODAFileConverter.exe"
```

Then close PowerShell, open a new one, `cd $HOME\dwg-verifier`, and run the `--deep` check again.

### 6. Optional: run the tests

```powershell
uv run pytest
```

It should end with `passed`. The Tier 2 tests show as `skipped` if the ODA File Converter isn't
installed.

### 7. Update to a newer version later

```powershell
cd $HOME\dwg-verifier
git pull
uv sync --extra deep
```

### Windows troubleshooting

| You see | Do this |
|---|---|
| `uv` or `git` "is not recognized as the name of a cmdlet" | Close PowerShell and open a new one. If it persists, rerun step 1. |
| `winget` "is not recognized" | Install Git from <https://git-scm.com/download/win> with the default options, then continue with the uv line of step 1. |
| `running scripts is disabled on this system` | You tried activating `.venv`. You don't need to: use `uv run dwgcheck ...` as shown. |
| `error: Failed to spawn: dwgcheck` | You're not in the project folder. Run `cd $HOME\dwg-verifier` first. |
| `no such file or directory` for your drawing | Check the path, and put it in quotes. |
| `ODA File Converter not found` | Follow the end of step 5 to set `DWGCHECK_ODAFC`. |
| `DWGCHECK_ODAFC=... does not point to a file` | The saved path is wrong. Repeat the `Get-ChildItem` and `setx` commands from step 5. |

## Usage

```
dwgcheck <file-or-dir> [--deep] [--json] [-r]
```

| Option | Meaning |
|---|---|
| `<file-or-dir>` | One file (any extension), or a directory, which checks its `*.dwg` files (case-insensitive) |
| `-r`, `--recursive` | Descend into subdirectories |
| `--deep` | Also run Tier 2 (ODA File Converter + ezdxf audit). Both tiers always run, so you see what each caught |
| `--json` | Machine-readable report on stdout |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every file is valid |
| 1 | At least one file is invalid |
| 2 | Nothing is invalid, but at least one file has an unsupported version (or is password-encrypted) |
| 3 | Tool or environment error: path missing, no DWG files found, unreadable file, ODA converter or ezdxf missing for `--deep`, or an internal error |

If several apply, the highest-priority one wins: 3, then 1, then 2.

### Output

Human output is one line per file, with findings listed under invalid files:

```
INVALID  cut.dwg (AC1024): 3 finding(s)
  [T1] T1.header.copy @ 0x6ddab: second header copy at 0x6ddab extends past end of file (0x36ee8); file is truncated
  [T1] T1.pagemap.bounds @ 0x6dcc0: section page map page header at 0x6dcc0 lies outside the file
  [T2] T2.oda.error @ ODA File Converter: OdError thrown during readFile of drawing "cut.dwg" : | Unexpected end of file
OK       good.dwg (AC1032) [T1+T2]
UNSUPPORTED old.dwg (AC1015): version string 'AC1015' is not AC1024, AC1027 or AC1032
```

Each finding has a file, tier, check id, location (byte offset and/or section name) and message.
Text that comes from the file is escaped before it reaches the terminal. `--json` produces:

```json
{
  "exit_code": 1,
  "files": [
    {
      "file": "cut.dwg",
      "status": "invalid",
      "version": "AC1024",
      "findings": [
        {
          "file": "cut.dwg",
          "tier": 1,
          "check": "T1.header.copy",
          "message": "second header copy at 0x6ddab extends past end of file (0x36ee8); file is truncated",
          "offset": 449963,
          "section": null
        }
      ],
      "tiers_run": [1],
      "note": null
    }
  ]
}
```

`status` is one of `valid`, `invalid`, `unsupported`, `error`.

## What is checked

### Tier 1: structural (pure Python, always runs)

Implemented from the ODA *Open Design Specification for .dwg files*, chapter 4 (R2004 format, which
chapters 6-8 say R2010-R2018 reuse unchanged). The checks run in order. A failure in steps 1-5 is
fatal, because the rest of the file can't be located without it. Data page failures are collected
for every page.

| # | Check ids | What |
|---|---|---|
| 1 | `T1.size` | File is at least the 0x100-byte file header |
| 2 | (unsupported) | Bytes 0-5 are `AC1024`, `AC1027` or `AC1032`; password-encrypted drawings are reported as unsupported |
| 3 | `T1.header.id`, `T1.header.crc` | Decrypt the 0x6C bytes at 0x80 (LCG XOR, spec 4.1); check `AcFssFcAJMB` and the CRC32 |
| 3b | `T1.header.copy`, `T1.trailing` | The header copy at the "second header address" exists, matches the primary, and is the last thing in the file |
| 4 | `T1.pagemap.*` | Section page map: inside the file, page type, compression type, checksum (spec 4.3), decompresses to the declared size, entries are well-formed |
| 5 | `T1.sectionmap.*` | Section map: the same checks, plus every description and page reference parses with nothing left over and no page is shared between sections |
| 6 | `T1.page.*` | Every data page: extent inside the file, unmasked header (spec 4.6) agrees with both maps, data and header checksums, start offsets in order, decompresses (spec 4.7) to exactly the section's page size; uncompressed pages store a full page |
| 7 | `T1.sections.required` | `AcDb:Header`, `AcDb:Classes`, `AcDb:Handles`, `AcDb:AcDbObjects` exist |

**Data after the end of the file counts as invalid** (`T1.trailing`). In every fixture, the
header copy ends at exactly the last byte. Extra bytes don't stop a reader, but they mean the file
isn't what the writer produced: typically a botched transfer, a concatenation, or an error page
appended by a download. A detect-only tool should say so.

Truncation is reported by `T1.header.copy` first, because the header copy is the last thing in
the file. The page-map and page-bounds checks catch it too when the tail survives but pages don't.

#### Where the spec and real files disagree

Both are noted in `src/dwgcheck/r2004.py` and were confirmed on all fixtures:

- **4.7, literal length**: the spec lists `0x01-0x0E : 4-0x12`, which is internally inconsistent.
  Files use `0x01-0x0F`, all meaning value + 3.
- **4.7, back-references**: the copy source is `compOffset + 1` bytes back, not `compOffset`.
  Without the +1, decompression fails on every fixture.

Everything else in chapter 4 matched the files as written, including both checksum orders, the
0x100 page-map address bias, the data page mask, and the CRC computed with its own field zeroed.
LibreDWG's source wasn't needed as a cross-reference; no LibreDWG code was consulted or copied.

### Tier 2: semantic (`--deep`)

1. Copy the file into a private temp dir. The original is never handed to the converter.
2. Run `ODAFileConverter <in> <out> ACAD2018 DXF 0 1 "*.DWG"` (audit on, recover mode off).
3. Every entry in the converter's `.err` report is a finding (`T2.oda.error`). No DXF produced is
   `T2.oda.convert`.
4. Load the DXF with `ezdxf.recover.readfile`. Auditor errors (`T2.ezdxf.errors`) and fixes
   (`T2.ezdxf.fixes`) are reported separately, with counts. Any fix is a finding: a file that only
   converts after silent repairs isn't clean.

`ezdxf.addons.odafc.convert()` does take an `audit` flag, but it discards the converter's `.err`
report. It also moves the first file in the output dir, which may be the `.err` file rather than
the DXF. So dwgcheck calls the converter directly.

## Detection rates

From `uv run python -m tools.detection_table --deep`, across the 22 fixtures. Seeds are fixed, so
the numbers are reproducible.

| Corruption | Variants | Tier 1 | Tier 2 | Either |
|---|---|---|---|---|
| `truncate_10`: truncated to 10% of size | 22 | 22/22 (100%) | 22/22 (100%) | 22/22 (100%) |
| `truncate_50`: truncated to 50% of size | 22 | 22/22 (100%) | 22/22 (100%) | 22/22 (100%) |
| `truncate_90`: truncated to 90% of size | 22 | 22/22 (100%) | 22/22 (100%) | 22/22 (100%) |
| `truncate_99`: truncated to 99% of size | 22 | 22/22 (100%) | 22/22 (100%) | 22/22 (100%) |
| `flip_header`: 1 random bit flipped in the encrypted file header | 110 | 110/110 (100%) | 110/110 (100%) | 110/110 (100%) |
| `flip_page_map`: 1 random bit flipped in the section page map page | 110 | 110/110 (100%) | 110/110 (100%) | 110/110 (100%) |
| `flip_section_map`: 1 random bit flipped in the section map page | 110 | 110/110 (100%) | 110/110 (100%) | 110/110 (100%) |
| `flip_data_page`: 1 random bit flipped in a random data page | 110 | 110/110 (100%) | 87/110 (79%) | 110/110 (100%) |
| `zero_data_page`: a whole random data page zeroed | 66 | 66/66 (100%) | 56/66 (85%) | 66/66 (100%) |
| `append_garbage`: 1-4096 random bytes appended after EOF | 66 | 66/66 (100%) | 0/66 (0%) | 66/66 (100%) |
| `semantic_objects`: object data altered with all checksums re-sealed | 66 | 0/66 (0%) | 63/66 (95%) | 63/66 (95%) |

Zero false positives: all 22 valid fixtures pass both tiers.

What the misses are:

- **Tier 2, `flip_data_page` / `zero_data_page`**: every miss is in `AcDb:Preview`,
  `AcDb:RevHistory` or `AcDb:FileDepList`. The converter doesn't carry those into the DXF, so it
  never notices damage there. Tier 2 caught damage in every other section.
- **Tier 2, `append_garbage`**: the converter ignores bytes after the end of the drawing.
- **Tier 1, `semantic_objects`**: by construction. This extra class (beyond the requested ones)
  edits object data and then re-seals every page checksum, so the container stays perfect. It
  exists to show what Tier 2 adds. Tier 1 doesn't parse objects, so it can't see per-object CRCs.
  The converter rejects most of these edits with "CRC does not match"; the 3 it accepted were
  edits inside live objects that it didn't flag.

## Known gaps

- **ODA audit fix count is not available.** With audit on, the converter repairs silently. Its
  CLI reports only exceptions (in `.err`), never what the audit changed, so dwgcheck can't
  report a fix count. Converting with audit off and on and diffing the output would expose
  repairs, but the output isn't byte-deterministic (header and ACIS timestamps change on every
  run), and no test file was available where ODA's audit repairs something, so this wasn't built.
- **Objects aren't parsed in Tier 1.** Per-object CRCs, the handle map's CRCs and the
  Header/Classes section CRCs aren't checked, so re-sealed object damage passes Tier 1 (see above).
- **Unprotected bytes.** The plain file header (0x06-0x7F: preview address, codepage, security
  flags, ...) and the padding between a page's data and the end of its extent aren't covered by
  any checksum, so changes there go unnoticed by both tiers. A flipped "encrypted" security flag
  makes a valid file report `unsupported`.
- **Unsupported inputs**: password-encrypted drawings, and every version other than the three
  above, including AC1018 (2004), which uses the same container.
- **Tier 2 has only been verified on macOS (arm64)** with ODA File Converter 27.9. The Linux
  (`xvfb-run`) and Windows paths follow ODA's documented layout but are untested, and so is the
  Windows guide above. On Windows the converter's window may appear briefly for each file.
- Files are read fully into memory.

## Fixtures

`fixtures/libredwg/` holds sample drawings from LibreDWG's
[`test/test-data`](https://github.com/LibreDWG/libredwg/tree/master/test/test-data):
`example_{2010,2013,2018}.dwg`, `sample_2018.dwg`, and
`{2010,2013,2018}/{Arc,Constraints,Helix,Leader,Line,Text}.dwg`.
