.PHONY: docker-up docker-down docker-build docker-logs

docker-up:
	docker compose down && docker compose up -d --build
	@echo ""
	@echo "Web UI available at: http://localhost:54718"

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f backend
