<p align="right">
  <a href="CONTRIBUTING.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Contributing

Thanks for your interest in improving the AI Passport Simulator!

## Ground rule

**Never fake device output.** The screen shows only framebuffer rows captured
from real running firmware. When output is unavailable, the UI reports the
real state and the real reason. PRs that render substitute or simulated
"app screens" will be rejected.

## Development setup

```bash
python3 -m venv web/.venv
. web/.venv/bin/activate
pip install -r web/requirements-dev.txt
```

A QEMU backend is not required for the unit test suites, but is needed for
manual end-to-end verification:

```bash
tools/fetch_qemu_runtime.sh --upstream
```

## Running the checks

```bash
pytest -q web/tests worker/test_worker.py   # all test suites
node --check web/app/static/app.js          # frontend syntax
bash -n tools/*.sh                          # script syntax
```

CI runs the same checks on Python 3.10 and 3.12.

## Documentation language rule

Documentation is bilingual: English at the default `.md` path and Simplified
Chinese at the matching `.zh_CN.md` path, with reciprocal links. If you change
behavior, update both sides.

## Submitting changes

1. Open an issue first for behavior changes so the design can be discussed.
2. Keep PRs focused; one logical change per PR.
3. Make sure the full test suite passes before submitting.
4. Describe not only what changed, but what you verified manually in the
   browser if the change affects UI or firmware execution.

## Reporting problems

Use the issue templates. For firmware-specific rendering problems, include the
firmware source (official catalog slug or your own build), the QEMU backend
you used, and the log panel output.
