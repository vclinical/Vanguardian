# Vanguardian

Vanguardian is an EDC-oriented clinical research platform for managing study data and core trial operations. The system is intended to help sponsors, CROs, study teams, data managers, and operational teams manage the research data lifecycle, from user access and site data entry to auditability, data quality controls, and operational visibility.

From a product perspective, Vanguardian aims to reduce data fragmentation, improve traceability, standardize access to sensitive research data, and shorten data-cleaning cycles before review, lock, or export milestones. From a technical perspective, the repository follows DDD and a modular monolith architecture, with clear bounded contexts such as `identity`, `audit`, `dashboard`, and shared platform components.

## Implementation Principles

- The default architecture is a modular monolith, and each bounded context must keep clear ownership and boundaries.
- Production business schema changes do not use Django migrations as the source of truth; production schema updates must go through `db/dbdiagram.dbml` and `db/migrations/*.sql`.
- Development environments may use Django migrations for local schema iteration and day-to-day manipulation, but release-ready business schema changes must be reconciled back to the production DB-first flow.

## Local Setup

The steps below reflect the current recommended local development flow for this repository.

### 1. Prepare the environment

Requirements:

- Python `3.14+`
- Docker and Docker Compose
- Local build tooling required by `mysqlclient`

Create a virtual environment and install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
``` 

### 2. Create the environment file

```bash
cp src/.env.tpl src/.env
```

By default, this file points to MariaDB and Memcached running on the host:

- MariaDB: `127.0.0.1:3306`
- Memcached: `127.0.0.1:11211`

### 3. Start local infrastructure

```bash
docker compose -f docker/docker-compose.yml up -d mariadb memcached
```

If you need the messaging service used by the current local stack, also start `mosquitto`:

```bash
docker compose -f docker/docker-compose.yml up -d mariadb memcached mosquitto
```

### 4. Initialize the database with the production-aligned DB-first flow

For day-to-day development, Django migrations may be used against a local database to iterate quickly. Use the DB-first flow below when validating a production-like database or preparing release-ready business schema changes.

Create Django foundation tables first:

```bash
python manage.py migrate contenttypes
python manage.py migrate auth
```

Apply all business SQL migrations from `db/migrations/` to MariaDB:

```bash
for file in db/migrations/*.sql; do
  docker exec -i vanguardian-mariadb \
    mariadb -uvanguardian -pvanguardian vanguardian < "$file" > /dev/null
done
```

Run the remaining framework-managed migrations:

```bash
python manage.py migrate
```
 
Create an admin account if you need quick access to the back office:

```bash
python manage.py createsuperuser
```

### 5. Run the application

```bash
python manage.py runserver
```
