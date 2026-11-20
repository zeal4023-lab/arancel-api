"""
Utilidad local: sube uno o varios archivos a GitHub en UN solo commit.

Uso:
    python scripts/commit_github.py "<mensaje>" archivo1.py archivo2.py ...
    python scripts/commit_github.py "<mensaje>" --branch feature/x archivo.py

Los archivos se suben conservando su ruta relativa desde la raíz del repo.
El token se lee de GH_TOKEN o de ~/.config/opencode/gh_token.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "zeal4023-lab"
REPO = "arancel-api"
DEFAULT_BRANCH = "main"
ROOT = Path(__file__).resolve().parents[1]
API = f"https://api.github.com/repos/{OWNER}/{REPO}"


def _token() -> str:
    """Token desde GH_TOKEN o, si no está, desde ~/.config/opencode/gh_token."""
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token
    archivo = Path.home() / ".config" / "opencode" / "gh_token"
    if archivo.exists():
        return archivo.read_text(encoding="utf-8").strip()
    raise SystemExit("Falta el token: define GH_TOKEN o crea ~/.config/opencode/gh_token")


def gh(method: str, url: str, payload: dict | None = None, *,
       not_found_ok: bool = False) -> dict | None:
    token = _token()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "arancel-local-tool",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if exc.code == 404 and not_found_ok:
            return None
        raise SystemExit(f"GitHub {method} {url} -> {exc.code}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sube archivos a GitHub en un solo commit."
    )
    parser.add_argument("mensaje")
    parser.add_argument("archivos", nargs="+",
                        help="'origen=>destino' o '!ruta/a/borrar'")
    parser.add_argument("--branch", default=DEFAULT_BRANCH,
                        help=f"Rama destino (por defecto {DEFAULT_BRANCH})")
    args = parser.parse_args()

    message = args.mensaje
    entradas = args.archivos
    branch = args.branch.strip() or DEFAULT_BRANCH

    repo = gh("GET", API)
    perms = repo.get("permissions", {})
    print(f"Repo: {repo['full_name']} | push={perms.get('push')}")

    ref = gh("GET", f"{API}/git/ref/heads/{branch}", not_found_ok=True)
    if ref is None:
        if branch == DEFAULT_BRANCH:
            raise SystemExit(f"No existe la rama {branch!r}")
        base = gh("GET", f"{API}/git/ref/heads/{DEFAULT_BRANCH}")
        parent = base["object"]["sha"]
        gh("POST", f"{API}/git/refs", {
            "ref": f"refs/heads/{branch}", "sha": parent,
        })
        print(f"Rama creada: {branch} desde {DEFAULT_BRANCH}@{parent[:7]}")
    else:
        parent = ref["object"]["sha"]
    commit = gh("GET", f"{API}/git/commits/{parent}")
    base_tree = commit["tree"]["sha"]
    print(f"Rama: {branch} | commit base: {parent[:7]}")

    tree = []
    for entrada in entradas:
        if entrada.startswith("!"):
            ruta = entrada[1:].strip()
            tree.append({"path": ruta, "mode": "100644", "type": "blob", "sha": None})
            print(f"  borrar {ruta}")
            continue
        if "=>" in entrada:
            origen, destino = (x.strip() for x in entrada.split("=>", 1))
        else:
            origen, destino = entrada, None
        path = Path(origen).resolve()
        if not path.exists():
            raise SystemExit(f"No existe: {path}")
        rel = destino or path.relative_to(ROOT).as_posix()
        blob = gh("POST", f"{API}/git/blobs", {
            "content": base64.b64encode(path.read_bytes()).decode(),
            "encoding": "base64",
        })
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  {path.name} -> {rel} ({blob['sha'][:7]})")

    new_tree = gh("POST", f"{API}/git/trees", {"base_tree": base_tree, "tree": tree})
    new_commit = gh("POST", f"{API}/git/commits", {
        "message": message, "tree": new_tree["sha"], "parents": [parent],
    })
    gh("PATCH", f"{API}/git/refs/heads/{branch}", {"sha": new_commit["sha"], "force": False})

    print(f"\nCOMMIT: {new_commit['sha'][:7]}  {new_commit['html_url']}")


if __name__ == "__main__":
    main()
