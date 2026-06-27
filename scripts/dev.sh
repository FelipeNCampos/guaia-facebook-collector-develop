#!/bin/bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.dev --project-name guaia-facebook-collector-dev "$@"
