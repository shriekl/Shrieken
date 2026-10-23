#!/usr/bin/env python3
"""GitHub Actions entry: scan due timeframes, encrypt, assemble dist/.

Requires SITE_PASSWORD. Exits before writing anything public if it is missing.
Plaintext scan files stay in build/raw and are not copied to dist/.
"""
import os, sys, json, argparse, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import schedule
import run_scan
import publish_util


def github_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"output {key}={value}", flush=True)
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def require_password():
    password = os.environ.get("SITE_PASSWORD", "")
    if password == "":
        print(
            "ERROR: SITE_PASSWORD secret is missing.\n"
            "Add it under Settings → Secrets and variables → Actions → New repository secret.\n"
            "Name it exactly SITE_PASSWORD. Use the password you want to type on the website.\n"
            "This workflow will not publish unencrypted signals.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return password


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=os.environ.get("SCAN_MODE", "due"))
    ap.add_argument("--symbols", default=os.environ.get("SCAN_SYMBOLS", ""))
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--build", default=os.path.join(ROOT, "build"))
    args = ap.parse_args()

    password = require_password()
    build = args.build
    prev_git = os.path.join(build, "prev-git")
    prev_pages = os.path.join(build, "prev-pages")
    raw = os.path.join(build, "raw")
    data = os.path.join(build, "data")
    os.makedirs(raw, exist_ok=True)

    repo = os.environ.get("GITHUB_REPOSITORY", "shriekl/Shrieken")
    os.chdir(ROOT)
    sources = []
    if publish_util.fetch_git_data(prev_git):
        sources.append(prev_git)
        print("recovered previous data from site-data branch", flush=True)
    if publish_util.fetch_pages_data(prev_pages, repo):
        sources.append(prev_pages)
        print("recovered previous data from GitHub Pages", flush=True)

    best, extras, metas = publish_util.merge_sources(sources)
    meta_tfs = {}
    for tf, (ts, info, path) in best.items():
        meta_tfs[tf] = info
    try:
        tfs = schedule.select(args.mode, meta_tfs)
    except SystemExit:
        raise
    if not tfs:
        print("No timeframes are due. Nothing to scan.", flush=True)
        github_output("deploy", "false")
        github_output("failed", "0")
        return

    print("scanning: " + ", ".join(tfs), flush=True)
    symbols = run_scan.universe_pairs([s for s in args.symbols.split(",") if s.strip()] or None)
    print(f"universe: {len(symbols)} pairs", flush=True)
    run_scan.prepare_source()

    state = {}
    state_path = extras["state.enc"][1]
    if state_path:
        state, _ = publish_util.load_state(state_path, password)
    track_changes = bool(state)

    fresh = {}
    failed = []
    for tf in tfs:
        try:
            payload, state = run_scan.scan_tf(tf, symbols, state, track_changes=track_changes)
        except Exception:
            failed.append(tf)
            traceback.print_exc()
            continue
        out = os.path.join(raw, f"{tf}.json")
        json.dump(payload, open(out, "w", encoding="utf-8"))
        fresh[tf] = payload
        print(
            f"{tf}: {len(payload['hits'])} setups, {payload['errors']} errors, "
            f"{payload['runtime_s']}s, sources {payload['sources']}",
            flush=True,
        )

    if not fresh and failed:
        print("ERROR: every timeframe failed. Previous site was left unchanged.", file=sys.stderr)
        github_output("deploy", "false")
        github_output("failed", "1")
        raise SystemExit(1)

    publish_util.build_data_dir(data, sources, fresh=fresh, password=password)
    publish_util.save_state(os.path.join(data, "state.enc"), state, password)
    publish_util.assemble_site(args.dist, os.path.join(ROOT, "site"), data)
    publish_util.assert_public_safe(args.dist)
    github_output("deploy", "true")
    github_output("failed", "1" if failed else "0")
    if failed:
        print("WARNING: failed timeframes: " + ", ".join(failed), flush=True)
        print("Successful timeframes were still packaged for publish.", flush=True)


if __name__ == "__main__":
    main()
