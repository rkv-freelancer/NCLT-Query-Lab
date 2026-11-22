.PHONY: sync lint fmt test check psql schema reset-db download profile
.PHONY: gaps spatial spatial-check bench-params bench bench-summary sweep explorer export report

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

loadbench:  ## make loadbench DATE=2012-01-08
	python -m nclt_lab.load_bench $(DATE) --repeat 3

# --- Step 1-5 ---------------------------------------------------------------
ingest:     ## make ingest DATE="2012-01-08" or make ingest DATE=--all
	python -m nclt_lab.main ingest $(DATE)

# --- Step 6 ---------------------------------------------------------------
gaps:       ## rebuild sensor_gaps and reconcile with profile.csv
	python -m nclt_lab.main gaps

# --- Step 7 (run `make spatial` first: it freezes the schema) -------------
spatial:    ## PostGIS + geom columns + gps_grid_stats (step 7.1 / 8)
	python -m nclt_lab.spatial apply

spatial-check:
	python -m nclt_lab.spatial check

bench-params: ## once: fixed query parameters in bench/params.json
	python -m nclt_lab.benchmarks params

bench:      ## make bench LABEL=baseline [SETUP=q1_btree] [QUERIES=q1,q5_lateral]
	python -m nclt_lab.benchmarks run --label $(LABEL) $(if $(SETUP),--setup $(SETUP)) $(if $(QUERIES),--queries $(QUERIES))

bench-summary:
	python -m nclt_lab.benchmarks summary $(if $(LABEL),--label $(LABEL))

# --- Step 9 ---------------------------------------------------------------
sweep:      ## resumable Explorer sweep (bench/sweep.toml)
	python -m nclt_lab.sweep

explorer:   ## runs.csv -> docs/explorer/index.html
	python -m nclt_lab.explorer

# --- Step 10 --------------------------------------------------------------
export:     ## Parquet per table per session -> data/export/ (D1's input)
	python -m nclt_lab.export

report:     ## regenerate README charts
	python -m nclt_lab.report
