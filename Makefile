# Otonom Finansal Danışman — geliştirici kısayolları (GNU make veya `just` benzeri kullanım)
PY ?= python

.PHONY: install lint format typecheck test cov gate run seed smoke snapshot docker-build up down

install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	ruff check .
	ruff format --check .

format:
	ruff check . --fix
	ruff format .

typecheck:
	mypy .

test:
	$(PY) -m pytest -q

cov:
	$(PY) -m pytest -q --cov --cov-report=term --cov-report=xml

# Faz sonu kalite kapısı
gate: lint typecheck cov

run:
	$(PY) main.py

seed:
	$(PY) scripts/seed_demo.py

smoke:
	$(PY) scripts/llm_smoke.py

snapshot:
	$(PY) scripts/fetch_real_data.py

docker-build:
	docker compose build

up:
	docker compose up --build

down:
	docker compose down
