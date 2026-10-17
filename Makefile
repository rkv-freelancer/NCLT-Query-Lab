.PHONY: sync lint fmt test check psql schema reset-db download profile

sync:       ## install exactly what uv.lock says
	uv sync --frozen

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .

test:
	pytest -q

check: lint test

psql:
	psql

schema:
	psql -v ON_ERROR_STOP=1 -f sql/schema.sql

reset-db:   ## drop every table; the container keeps running
	psql -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

download:   ## make download DATE=2012-01-08
	scripts/download.sh $(DATE)

profile:    ## make profile DATE=2012-01-08
	python -m nclt_lab.profile $(DATE)