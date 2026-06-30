"""
sources/github_source.py
-------------------------
Unstructured source #1: GitHub profile. Given a profile URL (or bare
username), hits the public REST API (api.github.com - no auth needed for
public data, subject to GitHub's unauthenticated rate limit) to pull name,
bio, repos and inferred languages/skills.

Network failures (rate limit, no connectivity, 404 user) are treated as a
soft failure -> RawRecord(ok=False), never a crash, per the robustness
constraint in the design.

IDENTITY-VERIFICATION SAFEGUARD
--------------------------------
A GitHub username is just a string the operator typed in — there's no
guarantee it actually belongs to the candidate being processed (wrong
handle, placeholder/test username like "octocat", a same-named stranger,
etc.). If we blindly merge whatever that profile says (bio, location,
language list) into the candidate record, we risk polluting the profile
with a stranger's data — which is exactly what happened when a real run
used a test username and the candidate silently inherited "San Francisco"
and "Ruby" from an unrelated GitHub account.

To guard against this, `extract()` accepts an optional `known_name` (the
candidate's name as already known from a higher-priority source, e.g.
recruiter_csv/ats_json). If provided and it does NOT loosely match the
GitHub profile's own `name` field, every field pulled from GitHub is
demoted into a `*_unverified` namespace instead of the real canonical
field name, so the merge step never lets it silently fill gaps (like an
empty location) with unrelated data. The raw github_url is still kept
under its normal field, since linking to a profile someone supplied
deliberately is harmless even if it's the wrong account — only
inferred/biographical fields are gated.
"""

from __future__ import annotations
import json
import re
import urllib.request
import urllib.error
from .base import BaseSource, RawRecord

_USER_RE = re.compile(r"github\.com/([A-Za-z0-9-]+)/?$")


def _username_from_input(raw: str) -> str:
    raw = raw.strip()
    m = _USER_RE.search(raw)
    if m:
        return m.group(1)
    return raw.rstrip("/").split("/")[-1]


def _http_get_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={"User-Agent": "candidate-transformer/1.0",
                                                "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_for_compare(name: str) -> str:
    """Loose-normalize a name for identity comparison: lowercase, strip
    punctuation, collapse whitespace."""
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def _username_to_name_tokens(username: str) -> set:
    """
    Best-effort: turn a GitHub username into name-like tokens, since many
    people use their real name as their handle (e.g. "NithyaSai-Dodla",
    "jane_doe", "JohnSmith92") even when they never bothered to fill in the
    separate "display name" field on their profile. Splits on hyphens,
    underscores, digits, and camelCase boundaries.
    """
    if not username:
        return set()
    # camelCase / PascalCase -> spaced
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", username)
    # hyphens/underscores/digits -> spaces
    spaced = re.sub(r"[-_0-9]+", " ", spaced)
    return set(_normalize_for_compare(spaced).split())


def _names_loosely_match(known_name: str, github_name: str, github_username: str = "") -> bool:
    """
    True if there's reasonable overlap between the candidate's known name
    and the GitHub profile's displayed name. Uses token overlap rather than
    exact match, since people order/abbreviate names differently across
    platforms (e.g. "Kuricheti Prerana" vs "Prerana K.").

    Falls back to comparing against the *username* when the profile has no
    display name set at all -- a blank display name is not evidence of a
    mismatch, it's just missing data, and many people's usernames already
    encode their real name (e.g. "NithyaSai-Dodla").
    """
    known_tokens = set(_normalize_for_compare(known_name).split())
    gh_tokens = set(_normalize_for_compare(github_name).split())
    if not gh_tokens:
        gh_tokens = _username_to_name_tokens(github_username)
    if not known_tokens or not gh_tokens:
        return False
    overlap = known_tokens & gh_tokens
    return len(overlap) >= 1


class GitHubSource(BaseSource):
    name = "github"

    def extract(self, profile_url_or_username: str, known_name: str | None = None) -> list:
        if not profile_url_or_username or not profile_url_or_username.strip():
            return [RawRecord(source_name=self.name, ok=False, error="empty github reference")]

        username = _username_from_input(profile_url_or_username)
        if not username:
            return [RawRecord(source_name=self.name, ok=False, error="could not parse username")]

        try:
            user = _http_get_json(f"https://api.github.com/users/{username}")
        except urllib.error.HTTPError as exc:
            return [RawRecord(source_name=self.name, ok=False, error=f"GitHub API HTTP {exc.code} for '{username}'")]
        except Exception as exc:  # noqa: BLE001
            return [RawRecord(source_name=self.name, ok=False, error=f"GitHub API unreachable: {exc}")]

        github_display_name = user.get("name") or ""
        github_url = user.get("html_url") or f"https://github.com/{username}"

        languages = set()
        top_repos: list[str] = []
        forked_repos: list[str] = []
        try:
            repos = _http_get_json(f"https://api.github.com/users/{username}/repos?per_page=100&sort=updated")
            for r in repos[:100]:
                if r.get("language"):
                    languages.add(r["language"])
                if not r.get("name"):
                    continue
                if r.get("fork"):
                    # Previously silently dropped. A profile made up entirely
                    # of forks (e.g. coursework/practice repos forked from
                    # classmates or instructors) would then show "no repos
                    # found" even though there's real signal here -- just
                    # under a different category than "owned, original work".
                    forked_repos.append(r["name"])
                else:
                    top_repos.append(r["name"])
            top_repos = top_repos[:10]  # cap at 10 most-recently-updated non-forks
            forked_repos = forked_repos[:10]
        except Exception:
            # Repo data is a bonus signal, not required - degrade gracefully.
            pass

        # --- Collaborated repos ------------------------------------------------
        # /users/{username}/repos only returns repos the user *owns* (including
        # forks). A contributor who pushes/opens PRs against someone else's repo
        # without owning a fork of it never shows up there at all -- which is
        # exactly the "she didn't have any repos of her own, she has collaborated
        # repos" case. There's no clean unauthenticated "repos I collaborate on"
        # endpoint, but the public events feed records pushes/PRs/issues against
        # repos regardless of ownership, so we use that as a best-effort proxy.
        collaborated_repos: list[str] = []
        try:
            events = _http_get_json(f"https://api.github.com/users/{username}/events/public?per_page=100")
            seen_collab = set()
            collab_event_types = {"PushEvent", "PullRequestEvent", "IssuesEvent", "PullRequestReviewEvent"}
            for ev in events:
                if ev.get("type") not in collab_event_types:
                    continue
                repo_full_name = (ev.get("repo") or {}).get("name")  # "owner/repo"
                if not repo_full_name or "/" not in repo_full_name:
                    continue
                owner, _, repo_name = repo_full_name.partition("/")
                if owner.lower() == username.lower():
                    continue  # owned repo, already covered by top_repos
                if repo_full_name not in seen_collab:
                    seen_collab.add(repo_full_name)
                    collaborated_repos.append(repo_full_name)
            collaborated_repos = collaborated_repos[:10]
        except Exception:
            # Best-effort signal only (and the events feed is unauthenticated/
            # rate-limited and only covers recent public activity) - never block
            # the rest of the extraction on it.
            pass

        # --- Identity corroboration gate -------------------------------------
        # If we have a known_name from a higher-priority structured source
        # (CSV / ATS), only trust GitHub data when the names loosely match.
        # If no known_name is available at all (standalone GitHub URL scenario),
        # we have nothing to contradict, so allow data through — there is no
        # cross-source conflict possible.
        if known_name:
            verified = _names_loosely_match(known_name, github_display_name, username)
        else:
            verified = True  # no contradicting name; trust the profile

        # The profile link itself is always safe to attach -- it's a
        # reference the operator explicitly supplied, not an inferred fact.
        fields = {"github_url": github_url}

        if verified:
            fields["name"] = github_display_name or username
            fields["headline"] = user.get("bio")
            fields["location_raw"] = user.get("location")
            fields["languages"] = sorted(languages)
            fields["repos_count"] = user.get("public_repos")
            fields["repos"] = top_repos
            fields["forked_repos"] = forked_repos
            fields["collaborated_repos"] = collaborated_repos
        else:
            # Demote unverifiable biographical data so the merge step never
            # uses it to silently fill empty canonical fields like location.
            # Repo/language data is included here too (under _unverified
            # names) for consistency -- previously only languages leaked
            # through for unverified profiles while repos/collaborated_repos
            # were dropped entirely, which made it look like the account had
            # no repo activity at all even when it did.
            fields["name_unverified"] = github_display_name or username
            fields["headline_unverified"] = user.get("bio")
            fields["location_raw_unverified"] = user.get("location")
            fields["languages_unverified"] = sorted(languages)
            fields["repos_count_unverified"] = user.get("public_repos")
            fields["repos_unverified"] = top_repos
            fields["forked_repos_unverified"] = forked_repos
            fields["collaborated_repos_unverified"] = collaborated_repos
            fields["identity_warning"] = (
                f"GitHub user '{username}' (display name "
                f"{github_display_name if github_display_name else 'none'!r}) could not be "
                f"corroborated against the candidate's known name; bio/location/languages "
                f"withheld from canonical fields."
            )

        fields = {k: v for k, v in fields.items() if v not in (None, "", [])}
        return [RawRecord(source_name=self.name, fields=fields)]