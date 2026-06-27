#!/bin/bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.train --project-name guaia-facebook-collector-train "$@"
