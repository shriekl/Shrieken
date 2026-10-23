#!/usr/bin/env python3
"""Assemble the static site for GitHub Pages without scanning.

Uses whatever encrypted files already exist. Does not need SITE_PASSWORD and
never writes plaintext signals.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import publish_util


def main():
    build = os.path.join(ROOT, "build")
    prev_git = os.path.join(build, "prev-git")
    prev_pages = os.path.join(build, "prev-pages")
    data = os.path.join(build, "data")
    dist = os.path.join(ROOT, "dist")
    repo = os.environ.get("GITHUB_REPOSITORY", "shriekl/Shrieken")
    os.chdir(ROOT)
    sources = [os.path.join(ROOT, "site", "data")]
    if publish_util.fetch_git_data(prev_git):
        sources.append(prev_git)
    if publish_util.fetch_pages_data(prev_pages, repo):
        sources.append(prev_pages)
    publish_util.build_data_dir(data, sources)
    publish_util.assemble_site(dist, os.path.join(ROOT, "site"), data)
    publish_util.assert_public_safe(dist)
    print(f"assembled {dist}", flush=True)


if __name__ == "__main__":
    main()
