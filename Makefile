.PHONY: install test build demo docker

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

build:
	python -m build

demo:
	jev-tree-demo --host 127.0.0.1 --port 8765

docker:
	docker compose up --build
