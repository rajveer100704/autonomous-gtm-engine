# GTM Engine V4 Developer Makefile

.PHONY: up down test lint benchmark clean

up:
	docker-compose up --build -d

down:
	docker-compose down

test:
	python -m pytest -v --tb=short

lint:
	python -m flake8 gtm_engine || echo "Flake8 not installed or lint errors found"

benchmark:
	python -m gtm_engine.benchmark.report

clean:
	rm -f test_gtm*.db test_graph_state*.db
