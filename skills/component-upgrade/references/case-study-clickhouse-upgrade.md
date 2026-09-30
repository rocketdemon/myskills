# Case Study: ClickHouse 26.5.1 → 26.7.1 Upgrade

> Anonymized. ClickHouse is the component being upgraded (a public product, kept
> as-is). The application that depends on ClickHouse, together with its compose
> services, container, and data volume, is referred to as *the dependent app*
> (`APP_SERVICE_WEB` / `APP_SERVICE_WORKER`, `CONTAINER_NAME`, `VOLUME_PATH`).
>
> Provenance: recorded from a real single-node upgrade. The conclusion passed two
> rounds of independent verification plus an adversarial re-check before it was
> written down.

## 1. Environment baseline

| Item | Value |
|------|-------|
| Deployment | Docker Compose (`clickhouse/clickhouse-server:latest`, to be pinned to `:26.7.1`) |
| Current version | 26.5.1 (build 882) |
| Target version | 26.7.1 |
| Topology | Single node, embedded Keeper (not a ZooKeeper cluster); Keeper listens on port 9181 |
| Table engine | `ReplicatedReplacingMergeTree` (the `ReplicatedMergeTree` family; the dependent app's default), but only **one** replica |
| Data size | ~31 MB across 12 tables in the `default` database (`observations` ~27 MB, `traces` ~3 MB, plus the rest) |
| Dependent app | The dependent app (self-hosted v3) requires ClickHouse ≥24.3 with no upper bound; runs as two compose services (`APP_SERVICE_WEB`, `APP_SERVICE_WORKER`), each gated on `condition: service_healthy` |
| Custom config | `clickhouse-system-ttl.xml` (14-day TTL on system logs, 6 tables) and `clickhouse-keeper.xml` (Keeper port 9181) |
| Image registry | `daemon.json` configures 4 registry mirrors; image pulls can be slow/unreliable, so budget for them |
| Downtime window | ~2–3 minutes |

## 2. Expected payoff (why upgrade at all)

| Version | Performance optimizations | Bug fixes | Key improvements |
|---------|:---:|:---:|------------------|
| 26.6 | 79 | 366 | Keeper ~2× faster, complex queries up to 64× faster, improved merge scheduling |
| 26.7 | 117 | — | Query optimization, memory management ("massive summer release") |
| **Total** | **~200** | **~600** | |

This upgrade indirectly mitigates merge storms; the root fix only comes with the
dependent app v4's immutable wide-table model.

Additional benefit: it satisfies the dependent app v4's prerequisite (minimum
ClickHouse ≥25.12, recommended ≥26.4).

## 3. Breaking-change audit

Backward-incompatible changes across 26.5 → 26.7:

| Version | Backward-incompatible change | Affects us? |
|---------|------------------------------|:---:|
| 26.6 | S3 credential auto-resolution removed (the server no longer resolves its own cloud credentials from the S3 URL) | No — object-store credentials are supplied through the dependent app, not auto-resolved by the server |
| 26.6 | `allow_experimental_query_deduplication` removed | No |
| 26.6 | Query-formatting alias-substitution inconsistency fixed | No — display-only change; the dependent app generates its own SQL |
| 26.6 | `mergeTreeAnalyzeIndexes` parameter changed to an array | No |
| 26.7 | No key breaking change | — |

**Conclusion: no impact.**

## 4. Known issue #103398 ruled out

GitHub issue **#103398** describes a Keeper changelog `CORRUPTED_DATA` failure that
occurs when upgrading a **multi-node cluster** across major versions
(24.x/25.x → 26.x) coordinated by an **external** Keeper/ZooKeeper.

Symptoms reported in the issue:

```
Code: 246. DB::Exception: Some records were lost, last committed log index 0
Code: 246. DB::Exception: Unknown version of serialization infos (1)
```

Why it does not apply here:

1. We stay **inside 26.x** (26.5 → 26.7) — we never cross the 25 → 26 boundary.
2. **Single node** — there is no cluster coordination and no cross-node changelog to desync.
3. **Embedded Keeper** — there is no externally-managed changelog that could hit a version mismatch.
4. **Tiny dataset** (~31 MB vs. the production cluster in the issue).

**Conclusion: not applicable.**

## 5. Execution steps

```bash
cd "$APP_DIR"          # the compose stack directory

# 1. Pull the target version
docker pull clickhouse/clickhouse-server:26.7.1

# 2. Back up the data volume
sudo tar -czf /tmp/clickhouse_backup_$(date +%Y%m%d).tar.gz \
  -C /var/lib/docker/volumes/VOLUME_PATH/_data .

# 3. Stop the dependent app first
#    (otherwise it logs write errors while ClickHouse is unavailable)
docker compose stop APP_SERVICE_WEB APP_SERVICE_WORKER

# 4. Stop ClickHouse
docker compose stop clickhouse

# 5. Pin the version in docker-compose.yml
sed -i 's|image: clickhouse/clickhouse-server:latest|image: clickhouse/clickhouse-server:26.7.1|' docker-compose.yml

# 6. Start the new version
docker compose up -d clickhouse

# 7. Wait for the health check (10 retries × 5 s = up to 50 s)
#    watch: docker compose ps clickhouse

# 8. Verify
docker exec CONTAINER_NAME clickhouse-client \
  --user clickhouse --password clickhouse -q "SELECT version()"
#   expect: 26.7.x

docker exec CONTAINER_NAME clickhouse-client \
  --user clickhouse --password clickhouse -q "SELECT count() FROM observations"
#   expect: 11217

docker exec CONTAINER_NAME clickhouse-client \
  --user clickhouse --password clickhouse -q "SELECT count() FROM traces"
#   expect: 842

docker exec CONTAINER_NAME clickhouse-client \
  --user clickhouse --password clickhouse -q "SELECT count() FROM system.merges"

# 9. Watch the logs for ~2 minutes (specifically for Keeper CORRUPTED_DATA)
docker logs --tail 50 CONTAINER_NAME

# 10. Restart the dependent app
docker compose up -d APP_SERVICE_WEB APP_SERVICE_WORKER

# 11. Verify the dependent app
curl -s http://APP_HOST:APP_PORT/api/health      # expect HTTP 200
```

## 6. Verification checklist

- [ ] `SELECT version()` → 26.7.x
- [ ] `observations` row count = 11,217
- [ ] `traces` row count = 842
- [ ] Background merges report no `CORRUPTED_DATA` errors
- [ ] Container logs free of `ERROR` (after observing for 2 minutes)
- [ ] The dependent app's health endpoint returns HTTP 200

## 7. Rollback

```bash
cd "$APP_DIR"
docker compose stop APP_SERVICE_WEB APP_SERVICE_WORKER clickhouse

sudo rm -rf /var/lib/docker/volumes/VOLUME_PATH/_data/*
sudo tar -xzf /tmp/clickhouse_backup_YYYYMMDD.tar.gz \
  -C /var/lib/docker/volumes/VOLUME_PATH/_data/

sed -i 's|image: clickhouse/clickhouse-server:26.7.1|image: clickhouse/clickhouse-server:latest|' docker-compose.yml
docker compose up -d clickhouse APP_SERVICE_WEB APP_SERVICE_WORKER
```

## 8. Post-upgrade compatibility and the next major version

Upgrading to 26.7 satisfies the dependent app v4's minimum requirement
(≥25.12, ≥26.4 recommended).

Key points for the eventual v4 migration:

- v4 replaces the ReplacingMergeTree tables with an **immutable wide table**
  (`events_full`) → this eliminates merge storms at the root.
- It needs roughly **3× the ClickHouse disk headroom** free for the backfill.
- Three-phase migration: upgrade ClickHouse → deploy v4 in **dual-write** mode →
  backfill → cutover.
- Architecture is unchanged (the same set of Docker components).
- v3 remains on security-patch support, so there is no urgency to move.

Status when this was written: v4.0.0-rc.0 had been released, but self-hosted
migration tooling for existing data was not ready yet. Wait for a properly
tagged image **plus** the migration tooling before touching the stack.
