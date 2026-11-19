"""Refresh the terminal card with public GitHub metrics."""

import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "assets" / "terminal.svg"
PORTRAIT = ROOT / "assets" / "portrait.txt"
USER = "songshuhan"
LAB = "GTLSysGraph"
LAB_PROJECTS = {
    "ALOHA", "DynGraphSurvey", "FSLNet", "FilterMGAE", "GPromptShield", "SemFuse", "SPMGAE"
}
CODE_PROJECTS = {"ALOHA", "FilterMGAE", "GPromptShield", "SemFuse", "SPMGAE"}
EXCLUDED_CODE = Path("src/llm/llama_factory")
SVG_NS = "{http://www.w3.org/2000/svg}"
ASCII_RAMP = ".,:;iloxvncO0MW@"


def api(path, **query):
    url = f"https://api.github.com/{path}"
    if query:
        url += "?" + urlencode(query)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "songshuhan-profile-card",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def public_repos(path, repo_type):
    repos = []
    page = 1
    while True:
        batch = api(path, type=repo_type, per_page=100, page=page)
        repos.extend(repo for repo in batch if not repo["private"])
        if len(batch) < 100:
            return repos
        page += 1


def read_cache():
    if not CARD.exists():
        return {}
    metadata = ElementTree.parse(CARD).getroot().find(SVG_NS + "metadata")
    return json.loads(metadata.text) if metadata is not None and metadata.text else {}


def git(*args, directory=None):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    return subprocess.check_output(
        ["git", *args], cwd=directory, env=environment, text=True, timeout=120
    ).strip()


def line_count(repo, cache):
    name = repo["name"]
    branch = repo["default_branch"]
    version = api(f"repos/{LAB}/{name}/branches/{quote(branch, safe='')}")["commit"]["sha"]
    previous = cache.get(name, {})
    if previous.get("commit") == version:
        return name, previous

    with tempfile.TemporaryDirectory(prefix="profile-loc-") as scratch:
        git("clone", "--quiet", "--depth=1", "--filter=blob:none", "--no-checkout",
            "--branch", branch, f"https://github.com/{LAB}/{name}.git", scratch)
        git("sparse-checkout", "set", "--no-cone", "*.py", directory=scratch)
        git("checkout", "--quiet", directory=scratch)
        actual_version = git("rev-parse", "HEAD", directory=scratch)
        tree = Path(scratch)
        total = 0
        for file in tree.rglob("*.py"):
            if not file.is_file() or file.is_symlink():
                continue
            if name == "SemFuse" and file.relative_to(tree).is_relative_to(EXCLUDED_CODE):
                continue
            with file.open("rb") as source:
                total += sum(bool(line.strip()) for line in source)
    return name, {"commit": actual_version, "lines": total}


def collect_stats():
    with ThreadPoolExecutor(max_workers=4) as executor:
        personal_future = executor.submit(public_repos, f"users/{USER}/repos", "owner")
        lab_future = executor.submit(public_repos, f"orgs/{LAB}/repos", "public")
        commits_future = executor.submit(api, "search/commits",
                                         q=f"author:{USER} is:public", per_page=1)
        personal = personal_future.result()
        lab_public = lab_future.result()
        lab = [repo for repo in lab_public if repo["name"] in LAB_PROJECTS]
        commits = commits_future.result()
    if commits["incomplete_results"]:
        raise RuntimeError("GitHub returned partial commit-search results.")
    if {repo["name"] for repo in lab} != LAB_PROJECTS:
        raise RuntimeError("An expected lab project is no longer listed as public.")

    cache = read_cache()
    code_repos = [repo for repo in lab if repo["name"] in CODE_PROJECTS]
    with ThreadPoolExecutor(max_workers=5) as executor:
        line_counts = dict(executor.map(lambda repo: line_count(repo, cache), code_repos))
    stats = {
        "personal_repos": len(personal),
        "lab_repos": len(lab),
        "lab_stars": sum(repo["stargazers_count"] for repo in lab_public),
        "commits": commits["total_count"],
        "lines": sum(entry["lines"] for entry in line_counts.values()),
    }
    return stats, line_counts


def portrait_svg():
    rows = PORTRAIT.read_text(encoding="ascii").splitlines()
    glyphs = []
    for row, line in enumerate(rows):
        for col, character in enumerate(line):
            if character == " ":
                continue
            tone = ASCII_RAMP.index(character) / (len(ASCII_RAMP) - 1)
            channels = [round(a + (b - a) * tone) for a, b in zip((94, 126, 158), (223, 248, 231))]
            color = "#" + bytes(channels).hex()
            x, y = 17 + col * 4.52, 62 + row * 6.65
            glyphs.append(f'<text x="{x:.2f}" y="{y:.2f}" fill="{color}">{escape(character)}</text>')
    return "\n    ".join(glyphs)


def render(stats, cache):
    research = [
        ("Graph ML", 291, 157),
        ("Self-supervised learning", 529, 157),
        ("Robust graph learning", 291, 181),
        ("Recommender systems", 529, 181),
    ]
    research_svg = "\n    ".join(
        f'''<rect x="{x}" y="{y}" width="228" height="19" rx="4" class="topic-box"/>
    <path d="M {x + 9},{y + 7} h 4 v 4 h -4 z" fill="#83e4c7"/>
    <text x="{x + 21}" y="{y + 14}" class="topic">{label}</text>'''
        for label, x, y in research
    )
    metrics = [
        ("Repos", f"{stats['personal_repos']} / {stats['lab_repos']}", "personal / lab"),
        ("Stars", f"{stats['lab_stars']:,}", "HTAI-Lab total"),
        ("Commits", f"{stats['commits']:,}", "public authored"),
        ("Python LOC", f"{stats['lines']:,}", "public lab code"),
    ]
    cells = []
    for column, (label, value, scope) in enumerate(metrics):
        x = 291 + column * 119
        cells.append(f'''<text x="{x}" y="248" class="label">{label}</text>
    <text x="{x}" y="274" class="metric">{value}</text>
    <text x="{x}" y="294" class="scope">{scope}</text>''')
    stats_svg = "\n    ".join(cells)
    metadata = escape(json.dumps(cache, sort_keys=True))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="788" height="322" viewBox="0 0 788 322" role="img" aria-labelledby="title description">
  <title id="title">Shuhan Song — terminal profile</title>
  <desc id="description">ASCII portrait, ICT CAS research affiliation, research interests, contact, and public GitHub statistics.</desc>
  <metadata>{metadata}</metadata>
  <defs>
    <linearGradient id="shell" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop stop-color="#111a2b"/>
      <stop offset="1" stop-color="#101520"/>
    </linearGradient>
    <style>
      text {{ font-family: "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace; }}
      .prompt {{ fill: #83e4c7; font-size: 14px; font-weight: bold; }}
      .cmd {{ fill: #abb9d5; font-size: 14px; }}
      .name {{ fill: #f2f7fb; font-size: 27px; font-weight: bold; }}
      .role {{ fill: #b7a8f2; font-size: 15px; font-weight: bold; }}
      .body {{ fill: #c4d0e4; font-size: 14px; }}
      .topic {{ fill: #cfddf0; font-size: 12.5px; }}
      .topic-box {{ fill: #172338; stroke: #30445f; }}
      .label {{ fill: #9db1d1; font-size: 12px; }}
      .metric {{ fill: #e0f4ee; font-size: 21px; font-weight: bold; }}
      .scope {{ fill: #93a7c6; font-size: 10.5px; }}
    </style>
  </defs>
  <rect x="1" y="1" width="786" height="320" rx="16" fill="url(#shell)" stroke="#35445f" stroke-width="2"/>
  <path d="M 1,40 H 787 M 272,51 V 306 M 290,227 H 761" stroke="#304058" stroke-width="1"/>
  <text x="23" y="26" class="prompt">songshuhan@ict:~$</text>
  <text x="179" y="26" class="cmd">whoami</text>
  <rect x="232" y="14" width="7" height="13" rx="1" fill="#94e9cf">
    <animate attributeName="opacity" values="1;.25;1" dur="1.2s" repeatCount="3"/>
  </rect>
  <circle cx="729" cy="21" r="4" fill="#9b93d9"/>
  <circle cx="746" cy="21" r="4" fill="#6cb8cb"/>
  <circle cx="763" cy="21" r="4" fill="#80cfb5"/>
  <g font-size="7.45" xml:space="preserve" aria-hidden="true">
    {portrait_svg()}
  </g>
  <g>
    <text x="290" y="79" class="name">Shuhan Song</text>
    <text x="291" y="101" class="role">Assistant Researcher · ICT, CAS</text>
    <text x="291" y="122" class="body">High-Throughput Computing Systems Group</text>
    <text x="291" y="146" class="prompt">$ research</text>
    {research_svg}
    <text x="291" y="217" class="label">EMAIL</text>
    <text x="343" y="217" class="body">songshuhan@ict.ac.cn</text>
    {stats_svg}
  </g>
</svg>
'''


def main():
    stats, cache = collect_stats()
    content = render(stats, cache)
    ElementTree.fromstring(content)
    if not CARD.exists() or CARD.read_text() != content:
        CARD.write_text(content)
    print(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
