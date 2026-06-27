#!/bin/bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env.hom --project-name guaia-facebook-collector-hom "$@"
