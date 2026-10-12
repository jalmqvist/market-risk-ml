.PHONY: db-up test

db-up:
	docker compose up -d db

test:
	python -m pytest
