"""Build the public site directory. Plaintext signals never land in it."""
import os, sys, json, shutil, urllib.request
from pathlib import Path

import schedule
import crypto_payload

LEAK_MARKERS = (
    "pullback_entry",
    "RETEST_CONFIRMED_VOLUME",
    "anchor1_ms",
    "breakout_level",
    "SITE_PASSWORD",
)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def empty_meta():
    return {"v": 1, "encrypted": True, "status": "pending", "updated_ts": None, "updated_ist": None, "tfs": {}}


def refresh_next(meta, now=None):
    import time
    now_dt = schedule._utc(now)
    meta["updated_ts"] = int(now_dt.timestamp())
    meta["updated_ist"] = schedule.fmt_ist(now_dt)
    meta["encrypted"] = True
    for tf, info in (meta.get("tfs") or {}).items():
        nxt = schedule.next_slot(tf, now_dt)
        info["next_scan_ist"] = schedule.fmt_ist(nxt)
        info["next_scan_ts"] = int(nxt.timestamp())
    if meta.get("tfs"):
        meta["status"] = "ok"
    return meta


def fetch_pages_data(dest, repo):
    """Download the live Pages data files. They are ciphertext plus timestamps."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    owner, name = repo.split("/", 1)
    base = f"https://{owner}.github.io/{name}/data"
    try:
        import time
        meta_url = f"{base}/meta.json?ts={int(time.time())}"
        with urllib.request.urlopen(meta_url, timeout=20) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"pages data not fetched ({e})", flush=True)
        return False
    write_json(dest / "meta.json", meta)
    names = [f"{tf}.json" for tf in meta.get("tfs", {})]
    names += ["verify.json", "state.enc"]
    ok = False
    for name_ in names:
        url = f"{base}/{name_}?ts={meta.get('updated_ts') or 0}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                (dest / name_).write_bytes(r.read())
            ok = True
        except Exception as e:
            print(f"  skip {name_}: {e}", flush=True)
    return ok


def fetch_git_data(dest):
    """Copy data/ from the single-commit site-data branch, if it exists."""
    import subprocess, tarfile, io
    dest = Path(dest)
    fetch = subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", "site-data:refs/remotes/origin/site-data"],
        capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        print("site-data branch not fetched", flush=True)
        return False
    arch = subprocess.run(
        ["git", "archive", "refs/remotes/origin/site-data", "data"],
        capture_output=True,
    )
    if arch.returncode != 0 or not arch.stdout:
        print("site-data archive empty", flush=True)
        return False
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(arch.stdout), mode="r:") as tar:
        kwargs = {"filter": "data"} if sys.version_info >= (3, 12) else {}
        tar.extractall(dest, **kwargs)
    # git archive includes the top-level `data/` folder.
    extracted = dest / "data"
    if extracted.is_dir() and extracted != dest:
        for child in extracted.iterdir():
            target = dest / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(child), str(target))
        extracted.rmdir()
    return (dest / "meta.json").exists()


def _tf_file(directory, tf):
    p = Path(directory) / f"{tf}.json"
    return p if p.exists() else None


def merge_sources(sources):
    """Pick the newest encrypted file per timeframe across previous copies."""
    best = {}
    extras = {"verify.json": (0, None), "state.enc": (0, None)}
    metas = []
    for src in sources:
        src = Path(src)
        if not src.is_dir():
            continue
        meta = read_json(src / "meta.json") or {}
        metas.append((meta.get("updated_ts") or 0, src, meta))
        for tf, info in (meta.get("tfs") or {}).items():
            ts = info.get("generated_ts") or 0
            path = _tf_file(src, tf)
            if path and (tf not in best or ts >= best[tf][0]):
                best[tf] = (ts, info, path)
        for extra in extras:
            p = src / extra
            stamp = meta.get("updated_ts") or 0
            if p.exists() and stamp >= extras[extra][0]:
                extras[extra] = (stamp, p)
    return best, extras, metas


def build_data_dir(dest, sources, fresh=None, password=None, now=None):
    """Write dest with merged ciphertext, refreshed meta, and optional new scans.

    `fresh` maps tf -> plaintext payload dict. Those are encrypted with `password`.
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    best, extras, metas = merge_sources(sources)
    meta = empty_meta()
    if metas:
        metas.sort(key=lambda x: x[0])
        meta = metas[-1][2]
        meta.setdefault("tfs", {})
    if fresh:
        if not password:
            raise SystemExit("Refusing to write scan results without a password.")
        for tf, payload in fresh.items():
            crypto_payload.dump(dest / f"{tf}.json", crypto_payload.encrypt_json(payload, password))
            meta.setdefault("tfs", {})[tf] = {
                "generated_ts": payload["generated_ts"],
                "last_scan_ist": schedule.fmt_ist(payload["generated_ts"]),
                "next_scan_ist": schedule.fmt_ist(schedule.next_slot(tf, now)),
                "symbols": payload.get("symbols"),
                "errors": payload.get("errors"),
                "runtime_s": payload.get("runtime_s"),
                "sources": payload.get("sources") or [],
            }
            best.pop(tf, None)
        crypto_payload.dump(dest / "verify.json", crypto_payload.encrypt_json({"ok": True}, password))
    for tf, (ts, info, path) in best.items():
        shutil.copy2(path, dest / f"{tf}.json")
        meta.setdefault("tfs", {})[tf] = info
    if not (dest / "verify.json").exists() and extras["verify.json"][1]:
        shutil.copy2(extras["verify.json"][1], dest / "verify.json")
    if extras["state.enc"][1] and not (dest / "state.enc").exists():
        shutil.copy2(extras["state.enc"][1], dest / "state.enc")
    refresh_next(meta, now)
    write_json(dest / "meta.json", meta)
    return meta


def load_state(path, password):
    payload = read_json(path)
    if not payload or not password:
        return {}, False
    try:
        state = crypto_payload.decrypt_json(payload, password)
        if isinstance(state, dict):
            return state, True
    except Exception as e:
        print(f"state file could not be unlocked ({e}); starting a fresh change tracker", flush=True)
    return {}, False


def save_state(path, state, password):
    crypto_payload.dump(path, crypto_payload.encrypt_json(state, password))


def assemble_site(dist, site_dir, data_dir):
    dist, site_dir, data_dir = Path(dist), Path(site_dir), Path(data_dir)
    if dist.exists():
        shutil.rmtree(dist)
    shutil.copytree(site_dir, dist, ignore=shutil.ignore_patterns("data"))
    target = dist / "data"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(data_dir, target)
    (dist / ".nojekyll").write_text("")
    return dist


def assert_public_safe(dist):
    """Fail if published data files contain plaintext signal fields.

    The page script names those fields so it can read them after decryption.
    Only files under data/ are scanned. Base64 ciphertext cannot contain '_',
    and every signal marker we check includes an underscore.
    """
    data = Path(dist) / "data"
    leaks = []
    if data.is_dir():
        for path in data.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for marker in LEAK_MARKERS:
                if marker in text:
                    leaks.append(f"{path}: contains {marker}")
    if leaks:
        raise SystemExit("Refusing to publish. Plaintext signal fields found:\n" + "\n".join(leaks))
