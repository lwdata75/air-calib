# Les commandes du projet. « make sample » teste toute la chaîne en quelques minutes.
PY := uv run python

# macOS : LightGBM a besoin d'OpenMP. Sans Homebrew, on pointe vers la copie livrée avec scikit-learn
# (la variable doit être donnée directement à Python : `uv run` ne la transmet pas).
OPENMP := DYLD_FALLBACK_LIBRARY_PATH=$(shell .venv/bin/python -c "import sklearn, pathlib; print(pathlib.Path(sklearn.__file__).parent / '.dylibs')" 2>/dev/null)

.PHONY: install requirements data sample train notebooks test lint

install:  ## crée l'environnement à partir de uv.lock
	uv sync --frozen

requirements:  ## régénère requirements.txt depuis uv.lock
	uv export --frozen --no-hashes --no-dev --format requirements-txt -o requirements.txt

data:  ## téléchargement complet, nettoyage, variables (≈ 2 h, presque tout pour le téléchargement)
	$(PY) scripts/01_telecharger.py
	$(PY) scripts/02_nettoyer.py
	$(PY) scripts/03_construire_variables.py

sample:  ## la même chaîne, entraînement compris, sur 10 capteurs et un mois (quelques minutes)
	$(PY) scripts/01_telecharger.py --echantillon
	$(PY) scripts/02_nettoyer.py --echantillon
	$(PY) scripts/03_construire_variables.py --echantillon
	$(PY) scripts/04_entrainer.py --echantillon

train:  ## entraîne les modèles retenus sur toutes les stations (5 plis)
	$(PY) scripts/04_entrainer.py

notebooks:  ## réexécute les notebooks 01 à 04
	$(OPENMP) .venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/0[1-4]_*.ipynb

test:  ## tests unitaires (hors ligne)
	uv run pytest -q

lint:
	uv run ruff check src scripts tests notebooks
	uv run ruff format --check src scripts tests notebooks
