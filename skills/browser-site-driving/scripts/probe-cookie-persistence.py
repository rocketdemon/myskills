#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decide whether the cookies in a browser profile are "persisted to disk" (Chrome / Edge, read-only).

Why it's needed: tell "session cookies" (dropped as soon as the browser exits) apart from "persistent cookies" (written into
the on-disk profile, lost on neither shutdown nor a system restart) —— decide whether the login state survives a restart; **don't guess, read the database**.
Companion to SKILL.md §4.5.

Usage:
    python3 probe-cookie-persistence.py <profile-dir> [host-keyword]
Example:
    python3 probe-cookie-persistence.py ~/.config/google-chrome <target-site>
    python3 probe-cookie-persistence.py "$LOCALAPPDATA/Microsoft/Edge/User Data/Default"  # Windows-side layout

Output: for each hit — name / host / path / is_persistent / has_expires / expiry(CST) / secure / httpOnly / value byte count
Criterion: is_persistent=1 and expiry in the future  ⇒ persistent cookie ⇒ lost on neither browser close / shutdown / system restart
      is_persistent=0 or expiry≈0    ⇒ session cookie ⇒ dropped on a normal exit (the browser's "continue where you left off" may keep it)
Note: encrypted_value is v10/v11 encrypted —— deciding persistence does not require decryption;
      copy a copy first before reading (the browser holds a write lock, opening the original DB directly may hit the lock).
"""
import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile

CH_EPOCH_OFFSET = 11644473600  # seconds from 1601-01-01 → 1970-01-01


def to_local(expires_utc):
    if not expires_utc:
        return "—"
    ts = expires_utc / 1_000_000 - CH_EPOCH_OFFSET
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile_dir", help="profile root directory (containing Default/Cookies) or the Default directory itself")
    ap.add_argument("host_keyword", nargs="?", default="", help="only print cookies whose host contains this keyword; empty = all")
    a = ap.parse_args()

    d = os.path.expanduser(a.profile_dir)
    cands = [os.path.join(d, "Default", "Cookies"), os.path.join(d, "Network", "Cookies"),
             os.path.join(d, "Cookies")]
    src = next((p for p in cands if os.path.exists(p)), None)
    if not src:
        sys.exit("Cookies DB not found, tried:\n  " + "\n  ".join(cands))

    dst = os.path.join(tempfile.gettempdir(), "cookies_probe_copy.db")
    shutil.copy2(src, dst)  # read the copy to avoid the write lock
    con = sqlite3.connect(dst)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT name, host_key, path, is_persistent, has_expires, expires_utc, "
        "is_secure, is_httponly, length(encrypted_value) AS elen FROM cookies"))
    if a.host_keyword:
        rows = [r for r in rows if a.host_keyword in (r["host_key"] or "")]

    print("DB file: %s (%d bytes, mtime %s)" % (
        src, os.path.getsize(src),
        datetime.datetime.fromtimestamp(os.path.getmtime(src)).strftime("%Y-%m-%d %H:%M:%S")))
    print("%d cookies matched (keyword: %r)" % (len(rows), a.host_keyword))
    for r in rows:
        print("  name=%-18s host=%-28s path=%-4s persistent=%s has_expires=%s"
              % (r["name"], r["host_key"], r["path"], r["is_persistent"], r["has_expires"]))
        print("    expiry=%s   secure=%s httpOnly=%s   value bytes=%d"
              % (to_local(r["expires_utc"]), r["is_secure"], r["is_httponly"], r["elen"]))
    print("\nCriterion: persistent=1 + expiry in the future ⇒ persistent cookie (lost on neither shutdown nor a system restart);"
          "persistent=0 / expiry≈0 ⇒ session cookie (dropped on a normal exit).")
    print("Note: the value is encrypted and unreadable, but \"persistence\" is decided by the two columns above alone.")


if __name__ == "__main__":
    main()
