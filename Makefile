# Les commandes du projet. « make sample » teste toute la chaîne en quelques minutes.
PY := uv run python

.PHONY: install requirements data sample train test lint

install:  ## crée l'environnement à partir de uv.lock
	uv sync --frozen

requirements:  ## régénère requirements.txt depuis uv.lock
	uv export --frozen --no-hashes --no-dev --format requirements-txt -o requirements.txt

data:  ## téléchargement complet, nettoyage, variables (≈ 1 h)
	$(PY) scripts/01_telecharger.py
	$(PY) scripts/02_nettoyer.py
	$(PY) scripts/03_construire_variables.py

sample:  ## la même chaîne sur 10 capteurs et un mois
	$(PY) scripts/01_telecharger.py --echantillon
	$(PY) scripts/02_nettoyer.py --echantillon
	$(PY) scripts/03_construire_variables.py --echantillon

train:  ## entraîne les modèles retenus sur toutes les stations (5 plis)
	$(PY) scripts/04_entrainer.py

test:  ## tests unitaires (hors ligne)
	uv run pytest -q

lint:
	uv run ruff check src scripts tests
	uv run ruff format --check src scripts tests
