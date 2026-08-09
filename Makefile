SHELL := /bin/bash
VERSION ?= $(shell cat VERSION)
CHART := charts/jenkins-mcp-server
IMAGE ?= ghcr.io/grglzrv/jenkins-mcp-server

.PHONY: install lint typecheck test coverage build verify-version version helm-lint helm-template helm-validate helm-package docker-build integration validate-manifests clean

install:
	python -m pip install -e '.[dev]'

lint:
	ruff check .

typecheck:
	mypy src/

test:
	pytest -q

coverage:
	pytest --cov --cov-report=term-missing

build:
	python -m build
	twine check dist/*.whl dist/*.tar.gz

verify-version:
	python scripts/check_version.py
	python scripts/changelog.py validate

version:
	@test -n "$(VERSION)" || (echo "Usage: make version VERSION=X.Y.Z" && exit 1)
	python scripts/changelog.py prepare "$(VERSION)"
	python scripts/set_version.py "$(VERSION)"
	$(MAKE) verify-version

helm-lint:
	helm lint --strict $(CHART) --set jenkins.url=https://jenkins.example.com --set jenkins.credentials.create.jenkinsUserId=local --set jenkins.credentials.create.jenkinsApiToken=local-placeholder

helm-template:
	helm template jenkins-mcp $(CHART) --namespace jenkins-mcp --set jenkins.url=https://jenkins.example.com --set jenkins.credentials.create.jenkinsUserId=local --set jenkins.credentials.create.jenkinsApiToken=local-placeholder > /tmp/jenkins-mcp.yaml
	helm template jenkins-mcp $(CHART) --namespace jenkins-mcp -f examples/values/tailscale-production.yaml > /tmp/jenkins-mcp-production.yaml

helm-validate:
	./scripts/validate_helm_renders.sh $(CHART)

helm-package:
	mkdir -p dist
	helm package $(CHART) --destination dist

docker-build:
	docker build --build-arg APP_VERSION=$(VERSION) -t $(IMAGE):$(VERSION) .

integration:
	./integration/run.sh

validate-manifests:
	python scripts/validate_manifests.py

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
