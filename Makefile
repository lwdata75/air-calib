.PHONY: help setup requirements data sample train test lint format notebooks mlflow clean

RUN := uv run

help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Create the virtualenv from the lockfile
	uv sync --frozen

requirements:  ## Regenerate requirements.txt from uv.lock (never edit it by hand)
	uv export --frozen --no-hashes --no-dev --no-emit-project --format requirements-txt -o requirements.txt

data:  ## Run every implemented pipeline stage on the full period
	$(RUN) python -m aircalib all

sample:  ## Fast end-to-end run on a small subset
	$(RUN) python -m aircalib all --sample

train:  ## Step 6: model ladder, side studies on the subsample, final model (MLflow, about 1 h 30)
	for study in ladder ablation scale monotone unverifiable x optuna recalibration final; do \
		$(RUN) python -m aircalib train --study $$study || exit 1; \
	done

test:  ## Unit tests (offline)
	$(RUN) pytest -m "not network"

lint:  ## Lint + format check
	$(RUN) ruff check src tests notebooks
	$(RUN) ruff format --check src tests notebooks

format:  ## Auto-fix lint and format
	$(RUN) ruff check --fix src tests notebooks
	$(RUN) ruff format src tests notebooks

notebooks:  ## Re-execute every notebook in place
	$(RUN) jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb

mlflow:  ## Local MLflow UI (runs of step 6)
	$(RUN) mlflow ui --backend-store-uri sqlite:///mlflow.db

clean:  ## Remove caches (keeps data/)
	rm -rf .pytest_cache .ruff_cache **/__pycache__
