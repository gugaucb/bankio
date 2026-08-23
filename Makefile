.PHONY: up down migrate seed test verify shell e2e

up:
	docker compose up -d --build
	docker compose exec web python manage.py migrate --noinput
	docker compose exec web python manage.py seed_demo

down:
	docker compose down

migrate:
	docker compose exec web python manage.py makemigrations && docker compose exec web python manage.py migrate

seed:
	docker compose exec web python manage.py seed_demo

shell:
	docker compose exec web python manage.py shell_plus 2>/dev/null || docker compose exec web python manage.py shell

test:
	docker compose exec web pytest -q

verify:
	docker compose exec web sh -c "python manage.py check && python manage.py makemigrations --check --dry-run && pytest -q"

e2e:
	docker compose exec web pytest tests/e2e -q
