from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    roots = [
        ROOT / "deploy" / "kubernetes",
        ROOT / "deploy" / "argocd",
        ROOT / "examples" / "argocd",
    ]
    paths = [path for root in roots for path in sorted(root.rglob("*.yaml"))]
    paths += [path for root in roots for path in sorted(root.rglob("*.yml"))]
    failures: list[str] = []
    count = 0
    for path in paths:
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except Exception as exc:  # pragma: no cover
            failures.append(f"{path}: {exc}")
            continue
        for doc in docs:
            if doc is None:
                continue
            count += 1
            if not isinstance(doc, dict) or "apiVersion" not in doc or "kind" not in doc:
                failures.append(f"{path}: document missing apiVersion/kind")
    if failures:
        raise SystemExit("\n".join(failures))
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose.get("services", {}) if isinstance(compose, dict) else {}
    if set(services) != {"server", "minibridge"}:
        failures.append("compose.yaml: expected server and minibridge services")
    if failures:
        raise SystemExit("\n".join(failures))
    print(
        f"Validated {count} Kubernetes/Argo CD YAML documents from "
        f"{len(paths)} files and compose.yaml"
    )


if __name__ == "__main__":
    main()
