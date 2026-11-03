"""Dedupe logic for the single autopilot GitHub issue. Pure functions, no network (tests/test_ops_issue.py).

decide(failures, open_issues) -> {"action": "create"|"update"|"close"|"none", "number": int|None, "title", "body"}
  - failures: list of {"name", "detail"} that need action (empty = healthy)
  - open_issues: open issues carrying the autopilot label, [{"number","title","body"}]
One issue ever: if several are open (race), the lowest number is the keeper and the rest are listed in "dupes" to close.
An update only happens when the failing set changed (the body carries a fingerprint), so a still-broken site does not
re-notify every day.
"""
import hashlib
import re

TITLE = "irlpls autopilot: something needs attention"
LABEL = "autopilot"
FP = re.compile(r"<!-- fp:([0-9a-f]{12}) -->")


def fingerprint(failures):
    names = "\n".join(sorted(f["name"] for f in failures))
    return hashlib.sha256(names.encode()).hexdigest()[:12]


def body_for(failures):
    lines = ["The daily autopilot found problems. This issue closes itself when they clear.", ""]
    lines += [f"- **{f['name']}**: {f['detail']}" for f in sorted(failures, key=lambda f: f["name"])]
    lines += ["", f"<!-- fp:{fingerprint(failures)} -->"]
    return "\n".join(lines)


def decide(failures, open_issues):
    issues = sorted(open_issues, key=lambda i: i["number"])
    dupes = [i["number"] for i in issues[1:]]
    keeper = issues[0] if issues else None
    if not failures:
        if keeper:
            return {"action": "close", "number": keeper["number"], "title": TITLE, "body": "", "dupes": dupes}
        return {"action": "none", "number": None, "title": TITLE, "body": "", "dupes": []}
    body = body_for(failures)
    if not keeper:
        return {"action": "create", "number": None, "title": TITLE, "body": body, "dupes": []}
    m = FP.search(keeper.get("body") or "")
    if m and m.group(1) == fingerprint(failures):
        return {"action": "none", "number": keeper["number"], "title": TITLE, "body": body, "dupes": dupes}
    return {"action": "update", "number": keeper["number"], "title": TITLE, "body": body, "dupes": dupes}


def main():
    """CLI for the workflow: ops_issue.py status.json  (needs GH_TOKEN, GITHUB_REPOSITORY; uses the gh CLI)."""
    import json
    import os
    import subprocess
    import sys
    repo = os.environ["GITHUB_REPOSITORY"]
    status = json.load(open(sys.argv[1]))
    failures = [c for c in status["checks"] if c["status"] == "fail"]

    def gh(*a, inp=None):
        return subprocess.run(["gh", *a], capture_output=True, text=True, input=inp, check=True).stdout
    subprocess.run(["gh", "label", "create", LABEL, "--repo", repo, "--color", "B60205"], capture_output=True)
    opened = json.loads(gh("issue", "list", "--repo", repo, "--label", LABEL, "--state", "open",
                           "--json", "number,title,body"))
    d = decide(failures, opened)
    print(d["action"], d["number"])
    if d["action"] == "create":
        gh("issue", "create", "--repo", repo, "--label", LABEL, "--title", d["title"], "--body", d["body"])
    elif d["action"] == "update":
        gh("issue", "edit", str(d["number"]), "--repo", repo, "--body", d["body"])
        gh("issue", "comment", str(d["number"]), "--repo", repo, "--body", "Changed:\n\n" + d["body"])
    elif d["action"] == "close":
        gh("issue", "close", str(d["number"]), "--repo", repo, "--comment", "Autopilot: all checks pass again.")
    for n in d["dupes"]:
        gh("issue", "close", str(n), "--repo", repo, "--comment", "Duplicate of the autopilot issue.")


if __name__ == "__main__":
    main()
