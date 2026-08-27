COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: up down migrate seed test verify shell e2e bootstrap-admin

up:
	$(COMPOSE) up -d --build
	$(COMPOSE) exec web python manage.py migrate --noinput
	$(COMPOSE) exec web python manage.py seed_demo

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec web python manage.py makemigrations && $(COMPOSE) exec web python manage.py migrate

seed:
	$(COMPOSE) exec web python manage.py seed_demo

bootstrap-admin:
	$(COMPOSE) run --rm web python manage.py bootstrap_admin

shell:
	$(COMPOSE) exec web python manage.py shell_plus 2>/dev/null || $(COMPOSE) exec web python manage.py shell

test:
	$(COMPOSE) exec web pytest -q

verify:
	$(COMPOSE) exec web sh -c "python manage.py check && python manage.py makemigrations --check --dry-run && pytest -q"

e2e:
	$(COMPOSE) exec web pytest tests/e2e -q

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
