# Testing and validation

## Test suite

* 64 pytest modules in `tests/`. `conftest.py` builds the app on a temporary data directory with a segmenter whose
  weights path does not exist. The tests therefore never load installed models, need no model files, and keep the
  shipped config fingerprint. Analyses run on synthetic scenes and reference annotations.
* 1,095 test cases after parametrisation, plus 2 expected failures (`xfail`).
* Markers (`pyproject.toml`):
  * `every_class_assessed`: runs with the pre-v2.21 policy and the TEST-ONLY sag reference (measurement mechanics
    on synthetic scenes);
  * `test_reference`: runs with the v2.21–v2.23 policy and the TEST-ONLY reference (assessment mechanics).

  These references exist only inside the tests, never in `config/wireframe.yaml`.
* Some tests execute the frontend ES modules under Node and are skipped when `node` is absent:
  `test_s18_refinement`, `test_s8_sag_reference`, `test_v213_viewer_opacity`, `test_v221`.
* Fixtures: `tests/fixtures/heic/` holds HEIC / HEIF / AVIF samples, including the deliberate byte-identical
  `land.heic` / `land.heif` pair (extension handling), rotated and portrait images, and damaged files (`fake.heic`,
  `truncated.heic`). Other test images are generated in code (`app/devtools/synthetic.py`).

Run the tests:

```
python -m pytest -q                       # or, as validated: -o addopts="" -p no:cacheprovider -q
ruff check app tests scripts ml
```

Test modules are named after the version or session that introduced them (`test_v2xx_*`, `test_sN_*`). The
mapping from requirements to tests is in [REQUIREMENT_TRACEABILITY.md](REQUIREMENT_TRACEABILITY.md).

## End-to-end check (`scripts/e2e_check.py`)

The script drives a running server in Chrome through Playwright:

* uploads a photo through the Field Screener;
* checks the result at several widths and in dark mode;
* signs in to the admin portal and checks the inspection sheet, review start, wire selection and meter, wire search,
  the absence of the technical view, the reference scope (telecom 2 % / 5 %, never 3 %), the map, and that
  `/admin/system` returns 404;
* checks that a staff session unlocks nothing in the citizen assistant;
* marks its own upload as a test record.

It needs a staff account (`WIREFRAME_E2E_USER`, `WIREFRAME_E2E_PASSWORD`) and writes screenshots and the PDF to
`--out`. **Run it only against an isolated data directory, never the live one.**

```
python scripts/e2e_check.py --base http://127.0.0.1:8014 --photo <photo.jpg> --out <dir>
```

Other checks: `scripts/server_check.py` (health), `scripts/repeatability.py` (test-retest),
`scripts/stress_test_all_conditions.py` (degradations), `ml/stress.py` and `ml/benchmark.py` (research).

## Release validation (19 Nov 2026, commit 45873c3)

Performed after the approved repository cleanup and before the documentation commit, on macOS 26.0.1 / Apple M1 /
Python 3.13.2, in `~/WIREFRAME-s13`.

| Check | Result |
|---|---|
| Protected files: SHA-256 of all 576 canonical files vs the blobs of 45873c3 | **576 / 576 identical** |
| `pytest` | **1,095 passed, 2 xfailed**, 6 warnings (Starlette 422 rename), 109 s. Matches the baseline |
| `ruff check app tests scripts ml` | **All checks passed** |
| Clean startup (isolated copy of the database, port 8014) | Ready in about 10 s; `/api/health` status `ok`, mode `field`, model ready, no errors in the log |
| Model loading | All 12 manifest files `present` (verified by SHA-256 in the audit); segmenter loaded |
| Config fingerprint | `42bb4ddc7d89` |
| Browser end-to-end check | **0 failed checks, 0 page errors** (Field Screener at 360–1280 px and dark mode, admin, PDF, map) |
| Documentation links (`docs/engineering/*.md`) | All relative links and anchors resolve |

The live database (`~/WIREFRAME/var`) was not used. Every run used an isolated copy.
