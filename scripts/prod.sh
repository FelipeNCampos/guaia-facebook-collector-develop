#!/bin/bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.prod --project-name guaia-facebook-collector-prod "$@"
